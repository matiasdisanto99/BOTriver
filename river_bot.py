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
UBICACION_OBJETIVO = "centenario baja"
INTERVALO_MINUTOS = 10
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

            email_input = page.locator("input[type='email'], input[name='Email'], input[name='email'], input[placeholder*='correo'], input[placeholder*='email']").first
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

            # Buscar botones COMPRAR activos
            botones = page.locator("button, a").all()
            partidos_activos = []

            for boton in botones:
                try:
                    t = (boton.inner_text() or "").strip().upper()
                    if "COMPRAR" not in t:
                        continue
                    if not boton.is_visible() or not boton.is_enabled():
                        continue
                    clases = boton.get_attribute("class") or ""
                    disabled = boton.get_attribute("disabled")
                    if disabled or "disabled" in clases.lower():
                        continue
                    href = boton.get_attribute("href") or ""
                    if not href:
                        try:
                            href = page.evaluate("el => el.closest('a')?.href || ''", boton)
                        except Exception:
                            pass
                    partidos_activos.append({"href": href})
                except Exception:
                    continue

            if not partidos_activos:
                estado["estado"] = "Sin entradas disponibles"
                estado["detalle"] = ""
                return

            estado["estado"] = f"{len(partidos_activos)} partido(s) con entradas activas"

            for i, partido in enumerate(partidos_activos):
                href = partido["href"]
                if not href:
                    continue

                url_completa = "https://www.riverid.com.ar" + href if href.startswith("/") else href
                print(f"🔎 Partido {i+1}: {url_completa}")

                page.goto(url_completa, timeout=40000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                # Extraer nombre del partido
                titulo = page.title() or f"Partido {i+1}"
                detalle_lines.append(f"--- PARTIDO {i+1}: {titulo} ---")

                # Extraer todas las ubicaciones visibles
                ubicaciones = page.evaluate("""() => {
                    const resultados = [];
                    const all = document.querySelectorAll('*');
                    for (const el of all) {
                        if (el.children.length > 0) continue;
                        const texto = el.textContent.trim().toLowerCase();
                        if (texto.length > 3 && texto.length < 50 && (
                            texto.includes('belgrano') ||
                            texto.includes('centenario') ||
                            texto.includes('sivori') ||
                            texto.includes('san martin') ||
                            texto.includes('platea') ||
                            texto.includes('popular') ||
                            texto.includes('tribuna') ||
                            texto.includes('palco')
                        )) {
                            // Ver si hay botón comprar cerca
                            let p = el;
                            let disponible = false;
                            for (let i = 0; i < 8; i++) {
                                p = p.parentElement;
                                if (!p) break;
                                for (const b of p.querySelectorAll('button, a')) {
                                    const t = b.textContent.trim().toUpperCase();
                                    if ((t.includes('COMPRAR') || t.includes('SELECCIONAR'))
                                        && !b.disabled
                                        && !b.className.toLowerCase().includes('disabled')) {
                                        disponible = true;
                                        break;
                                    }
                                }
                                if (disponible) break;
                            }
                            resultados.push(texto + (disponible ? ' ✅' : ' ❌'));
                        }
                    }
                    return [...new Set(resultados)];
                }""")

                if ubicaciones:
                    for ub in ubicaciones:
                        detalle_lines.append(f"  {ub}")
                else:
                    detalle_lines.append("  (no se encontraron ubicaciones)")

                # Verificar Centenario Baja específicamente
                texto_partido = page.inner_text("body").lower()
                if UBICACION_OBJETIVO in texto_partido:
                    detalle_lines.append(f"  >> '{UBICACION_OBJETIVO}' ENCONTRADO en texto")
                else:
                    detalle_lines.append(f"  >> '{UBICACION_OBJETIVO}' NO encontrado en texto")

                detalle_lines.append("")

            estado["detalle"] = "\n".join(detalle_lines)

        except Exception as e:
            print(f"❌ Error: {e}")
            estado["estado"] = f"Error: {str(e)[:120]}"
        finally:
            browser.close()


def loop_bot():
    print("🤖 BOTriver diagnóstico de ubicaciones iniciado.")
    while True:
        try:
            chequear_entradas()
        except Exception as e:
            print(f"❌ Error en loop: {e}")
            estado["estado"] = f"Error: {str(e)[:80]}"
        print(f"⏳ Esperando {INTERVALO_MINUTOS} minutos...")
        time.sleep(INTERVALO_MINUTOS * 60)


if __name__ == "__main__":
    hilo_bot = threading.Thread(target=loop_bot, daemon=True)
    hilo_bot.start()
    print("🌐 Servidor web iniciado")
    iniciar_servidor()
