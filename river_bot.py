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

            # Imprimir HTML de todos los botones COMPRAR con su contexto
            info_botones = page.evaluate("""() => {
                const resultados = [];
                const all = document.querySelectorAll('button, a');
                for (const el of all) {
                    const texto = el.textContent.trim().toUpperCase();
                    if (texto.includes('COMPRAR')) {
                        resultados.push({
                            tag: el.tagName,
                            texto: el.textContent.trim(),
                            href: el.getAttribute('href') || el.href || '',
                            disabled: el.disabled || el.getAttribute('disabled'),
                            clases: el.className,
                            outerHTML: el.outerHTML.substring(0, 300),
                            parentHTML: el.parentElement ? el.parentElement.outerHTML.substring(0, 300) : ''
                        });
                    }
                }
                return resultados;
            }""")

            detalle_lines.append(f"Total botones COMPRAR: {len(info_botones)}")
            for i, b in enumerate(info_botones):
                detalle_lines.append(f"\n--- BOTÓN {i+1} ---")
                detalle_lines.append(f"Tag: {b['tag']}")
                detalle_lines.append(f"Texto: {b['texto']}")
                detalle_lines.append(f"href: {b['href']}")
                detalle_lines.append(f"disabled: {b['disabled']}")
                detalle_lines.append(f"clases: {b['clases'][:100]}")
                detalle_lines.append(f"HTML: {b['outerHTML'][:200]}")

            estado["detalle"] = "\n".join(detalle_lines)
            estado["estado"] = f"{len(info_botones)} botones COMPRAR encontrados"

        except Exception as e:
            print(f"❌ Error: {e}")
            estado["estado"] = f"Error: {str(e)[:120]}"
        finally:
            browser.close()


def loop_bot():
    print("🤖 BOTriver diagnóstico href iniciado.")
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
