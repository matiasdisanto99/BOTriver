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
INTERVALO_MINUTOS = 5
# ──────────────────────────────────────────────────────────────

estado = {"ultimo_chequeo": "Iniciando...", "estado": "OK", "detalle": ""}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        msg = f"BOTriver activo\nUltimo chequeo: {estado['ultimo_chequeo']}\nEstado: {estado['estado']}\n\nDetalle:\n{estado['detalle']}"
        self.wfile.write(msg.encode("utf-8"))

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()

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
        print(f"WhatsApp enviado: {mensaje}")
    except Exception as e:
        print(f"Error WhatsApp: {e}")


def chequear_entradas():
    estado["ultimo_chequeo"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    detalle_lines = []
    print(f"Chequeando... {estado['ultimo_chequeo']}")

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
            detalle_lines.append("Intentando login...")
            page.goto(LOGIN_URL, timeout=40000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            email_input = page.locator("input[type='email'], input[name='Email'], input[name='email']").first
            if not email_input.is_visible():
                detalle_lines.append("ERROR: campo email no encontrado")
                estado["estado"] = "Error login"
                estado["detalle"] = "\n".join(detalle_lines)
                return

            email_input.fill(RIVER_EMAIL)
            page.locator("input[type='password'], input[name='Password']").first.fill(RIVER_PASSWORD)
            page.locator("button[type='submit'], input[type='submit']").first.click()
            page.wait_for_load_state("domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)

            if "Login" in page.url:
                detalle_lines.append("ERROR: login fallido")
                estado["estado"] = "Error de login"
                estado["detalle"] = "\n".join(detalle_lines)
                return

            detalle_lines.append("Login OK")

            # CALENDARIO - esperar mas tiempo a que Blazor cargue
            page.goto(CALENDARIO_URL, timeout=40000, wait_until="domcontentloaded")
            for _ in range(20):
                count = page.evaluate("() => document.querySelectorAll('button').length")
                detalle_lines.append(f"Botones detectados: {count}")
                if count > 2:
                    break
                page.wait_for_timeout(1500)
            page.wait_for_timeout(5000)

            detalle_lines.append(f"URL calendario: {page.url}")

            # Contar botones COMPRAR
            info = page.evaluate("""() => {
                const todos = [];
                document.querySelectorAll('button').forEach(b => {
                    const t = b.textContent.trim().toUpperCase();
                    if (t === 'COMPRAR') {
                        todos.push({
                            disabled: b.disabled,
                            clases: b.className
                        });
                    }
                });
                return todos;
            }""")

            detalle_lines.append(f"Botones COMPRAR encontrados: {len(info)}")
            for j, b in enumerate(info):
                detalle_lines.append(f"  Boton {j+1}: disabled={b['disabled']}")

            activos = [b for b in info if not b['disabled']]
            detalle_lines.append(f"Botones COMPRAR activos: {len(activos)}")

            if len(activos) == 0:
                estado["estado"] = "Sin entradas disponibles"
            else:
                estado["estado"] = f"{len(activos)} partido(s) activos"

            estado["detalle"] = "\n".join(detalle_lines)

        except Exception as e:
            detalle_lines.append(f"ERROR: {e}")
            estado["estado"] = f"Error: {str(e)[:80]}"
            estado["detalle"] = "\n".join(detalle_lines)
        finally:
            browser.close()


def loop_bot():
    print("BOTriver diagnostico iniciado.")
    while True:https://github.com/matiasdisanto99/BOTriver/blob/main/river_bot.py
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
