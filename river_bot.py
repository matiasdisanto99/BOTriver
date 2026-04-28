import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from twilio.rest import Client
from playwright.sync_api import sync_playwright

# ─── CONFIGURACIÓN ────────────────────────────────────────────
RIVER_EMAIL = os.environ["RIVER_EMAIL"]
RIVER_PASSWORD = os.environ["RIVER_PASSWORD"]
TWILIO_SID = os.environ["TWILIO_SID"]
TWILIO_TOKEN = os.environ["TWILIO_TOKEN"]
TWILIO_WHATSAPP_FROM = os.environ["TWILIO_WHATSAPP_FROM"]
TWILIO_WHATSAPP_TO = os.environ["TWILIO_WHATSAPP_TO"]

LOGIN_URL = "https://login.riverid.com.ar/Account/Login"
CALENDARIO_URL = "https://www.riverid.com.ar/Tickets/ProximosPartidos/Calendario"
UBICACION_OBJETIVO = "Centenario Baja"
INTERVALO_MINUTOS = 3
# ──────────────────────────────────────────────────────────────

estado = {"ultimo_chequeo": "Iniciando...", "estado": "OK", "detalle": ""}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        msg = f"BOTriver activo\nUltimo chequeo: {estado['ultimo_chequeo']}\nEstado: {estado['estado']}\n\nDetalle:\n{estado['detalle']}"
        self.wfile.write(msg.encode("utf-8"))

    def log_message(self, format, *args):
        pass


def iniciar_servidor():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


def enviar_whatsapp(mensaje):
    try:
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        client.messages.create(body=mensaje, from_=TWILIO_WHATSAPP_FROM, to=TWILIO_WHATSAPP_TO)
        print(f"✅ WhatsApp enviado: {mensaje}")
    except Exception as e:
        print(f"❌ Error WhatsApp: {e}")


def chequear_entradas():
    estado["ultimo_chequeo"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    detalle_lines = []
    print(f"🔍 Chequeando... {estado['ultimo_chequeo']}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--single-process"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        try:
            # LOGIN
            page.goto(LOGIN_URL, timeout=40000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            email_input = page.locator("input[type='email'], input[name='Email'], input[name='email']").first
            if not email_input.is_visible():
                estado["estado"] = "Error: formulario de login no encontrado"
                return

            email_input.fill(RIVER_EMAIL)
            page.locator("input[type='password'], input[name='Password']").first.fill(RIVER_PASSWORD)
            page.locator("button[type='submit'], input[type='submit']").first.click()
            page.wait_for_load_state("domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)

            if "Login" in page.url:
                estado["estado"] = "Error de login"
                return

            # CALENDARIO
            page.goto(CALENDARIO_URL, timeout=40000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            # Obtener IDs de botones COMPRAR activos
            botones_activos = page.evaluate("""() => {
                const botones = [];
                document.querySelectorAll('button').forEach(b => {
                    if (b.textContent.trim().toUpperCase() === 'COMPRAR' && !b.disabled) {
                        botones.push(b.id);
                    }
                });
                return botones;
            }""")

            if not botones_activos:
                estado["estado"] = "Sin entradas disponibles"
                estado["detalle"] = ""
                return

            detalle_lines.append(f"Partidos con entradas: {len(botones_activos)}")
            estado["estado"] = f"{len(botones_activos)} partido(s) con entradas activas"

            for i, boton_id in enumerate(botones_activos):
                detalle_lines.append(f"\n--- PARTIDO {i+1} ---")

                # Volver al calendario y hacer click en el boton
                page.goto(CALENDARIO_URL, timeout=40000, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)

                boton = page.locator(f"#{boton_id}")
                if not boton.is_visible():
                    detalle_lines.append("Boton no visible al volver")
                    continue

                boton.click()
                page.wait_for_timeout(4000)
                url_despues = page.url
                detalle_lines.append(f"URL despues del click: {url_despues}")

                page.wait_for_timeout(3000)
                texto = page.inner_text("body")
                detalle_lines.append(f"Texto (500 chars): {texto[:500]}")

                if UBICACION_OBJETIVO in texto:
                    detalle_lines.append(f"ENCONTRADO: {UBICACION_OBJETIVO}")

                    disponible = page.evaluate(f"""() => {{
                        const all = document.querySelectorAll('*');
                        for (const el of all) {{
                            if (el.children.length > 0) continue;
                            if (el.textContent.trim() === '{UBICACION_OBJETIVO}') {{
                                let p = el;
                                for (let i = 0; i < 10; i++) {{
                                    p = p.parentElement;
                                    if (!p) break;
                                    for (const b of p.querySelectorAll('button, a')) {{
                                        const t = b.textContent.trim().toUpperCase();
                                        if ((t.includes('COMPRAR') || t.includes('SELECCIONAR') || t.includes('VER'))
                                            && !b.disabled
                                            && !b.className.toLowerCase().includes('disabled')) {{
                                            return true;
                                        }}
                                    }}
                                }}
                            }}
                        }}
                        return false;
                    }}""")

                    if disponible:
                        detalle_lines.append("CENTENARIO BAJA DISPONIBLE!")
                        mensaje = f"ENTRADAS DISPONIBLES - CENTENARIO BAJA\nCompra ahora: {url_despues}"
                        enviar_whatsapp(mensaje)
                        estado["estado"] = "ENTRADAS DISPONIBLES - CENTENARIO BAJA"
                    else:
                        detalle_lines.append("Centenario Baja encontrado pero sin boton activo")
                else:
                    detalle_lines.append(f"{UBICACION_OBJETIVO} NO encontrado")

            estado["detalle"] = "\n".join(detalle_lines)

        except Exception as e:
            print(f"Error: {e}")
            estado["estado"] = f"Error: {str(e)[:120]}"
            estado["detalle"] = str(e)
        finally:
            browser.close()


def loop_bot():
    print("BOTriver iniciado.")
    enviar_whatsapp("Bot iniciado. Te aviso cuando haya Centenario Baja disponible.")
    while True:
        try:
            chequear_entradas()
        except Exception as e:
            print(f"Error en loop: {e}")
            estado["estado"] = f"Error: {str(e)[:80]}"
        print(f"Esperando {INTERVALO_MINUTOS} minutos...")
        time.sleep(INTERVALO_MINUTOS * 60)


if __name__ == "__main__":
    hilo_bot = threading.Thread(target=loop_bot, daemon=True)
    hilo_bot.start()
    print("Servidor web iniciado")
    iniciar_servidor()
