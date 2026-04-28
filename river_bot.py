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

estado = {"ultimo_chequeo": "Iniciando...", "estado": "OK"}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        msg = f"BOTriver activo\nUltimo chequeo: {estado['ultimo_chequeo']}\nEstado: {estado['estado']}"
        self.wfile.write(msg.encode())

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
            print(f"🌐 Navegando a: {LOGIN_URL}")
            page.goto(LOGIN_URL, timeout=40000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            url_actual = page.url
            print(f"📍 URL actual después de cargar: {url_actual}")

            html = page.content()
            print(f"📄 HTML (primeros 500 chars): {html[:500]}")

            texto = page.inner_text("body")
            print(f"📝 Texto visible (primeros 300 chars): {texto[:300]}")

            # Buscar todos los inputs
            inputs = page.locator("input").all()
            print(f"🔎 Inputs encontrados: {len(inputs)}")
            for inp in inputs:
                try:
                    nombre = inp.get_attribute("name") or ""
                    tipo = inp.get_attribute("type") or ""
                    visible = inp.is_visible()
                    print(f"   - input name='{nombre}' type='{tipo}' visible={visible}")
                except Exception:
                    pass

            # Intentar login con lo que hay
            email_input = page.locator("input[type='email'], input[name='Email'], input[name='email'], input[placeholder*='correo'], input[placeholder*='email']").first
            if email_input.count() > 0 and email_input.is_visible():
                print("✅ Campo email encontrado, haciendo login...")
                email_input.fill(RIVER_EMAIL)
                page.locator("input[type='password'], input[name='Password']").first.fill(RIVER_PASSWORD)
                page.locator("button[type='submit'], input[type='submit']").first.click()
                page.wait_for_load_state("domcontentloaded", timeout=20000)
                page.wait_for_timeout(3000)
                print(f"📍 URL después del login: {page.url}")

                if "Login" in page.url:
                    estado["estado"] = "Error de login"
                    return

                print("✅ Login exitoso!")

                # Ir al calendario
                page.goto(CALENDARIO_URL, timeout=40000, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
                texto_cal = page.inner_text("body").lower()
                print(f"📅 Calendario (primeros 300 chars): {texto_cal[:300]}")

                # Buscar COMPRAR
                botones = page.locator("button, a").all()
                comprar_count = 0
                for boton in botones:
                    try:
                        t = (boton.inner_text() or "").strip().upper()
                        if "COMPRAR" in t and boton.is_visible():
                            clases = boton.get_attribute("class") or ""
                            disabled = boton.get_attribute("disabled")
                            print(f"🎫 COMPRAR encontrado - disabled={disabled} clases={clases[:50]}")
                            comprar_count += 1
                    except Exception:
                        pass
                print(f"Total COMPRAR encontrados: {comprar_count}")
                estado["estado"] = f"OK - {comprar_count} botones COMPRAR encontrados"
            else:
                print("❌ No se encontró campo de email")
                estado["estado"] = "Error: no se encontró formulario de login"

        except Exception as e:
            print(f"❌ Error: {e}")
            estado["estado"] = f"Error: {str(e)[:120]}"
        finally:
            browser.close()


def loop_bot():
    print("🤖 BOTriver diagnóstico iniciado.")
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
