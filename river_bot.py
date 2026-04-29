import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, date
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

estado = {"ultimo_chequeo": "Iniciando...", "estado": "OK"}

# Guarda las URLs de partidos ya notificados hoy
# Se resetea automáticamente cada día
partidos_notificados = {"fecha": str(date.today()), "urls": set()}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        msg = f"BOTriver activo\nUltimo chequeo: {estado['ultimo_chequeo']}\nEstado: {estado['estado']}"
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


def ya_notificado(url):
    # Resetear si cambió el día
    hoy = str(date.today())
    if partidos_notificados["fecha"] != hoy:
        partidos_notificados["fecha"] = hoy
        partidos_notificados["urls"] = set()
    return url in partidos_notificados["urls"]


def marcar_notificado(url):
    partidos_notificados["urls"].add(url)


def chequear_entradas():
    estado["ultimo_chequeo"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
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
            page.goto(LOGIN_URL, timeout=40000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            email_input = page.locator("input[type='email'], input[name='Email'], input[name='email']").first
            if not email_input.is_visible():
                estado["estado"] = "Error de login"
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
            for _ in range(20):
                count = page.evaluate("() => document.querySelectorAll('button').length")
                if count > 2:
                    break
                page.wait_for_timeout(1500)
            page.wait_for_timeout(5000)

            total_activos = page.evaluate("""() => {
                let count = 0;
                document.querySelectorAll('button').forEach(b => {
                    if (b.textContent.trim().toUpperCase() === 'COMPRAR' && !b.disabled) count++;
                });
                return count;
            }""")

            if total_activos == 0:
                estado["estado"] = "Sin entradas disponibles"
                return

            print(f"{total_activos} partido(s) con entradas activas")

            for i in range(total_activos):
                # Recargar calendario
                page.goto(CALENDARIO_URL, timeout=40000, wait_until="domcontentloaded")
                for _ in range(20):
                    count = page.evaluate("() => document.querySelectorAll('button').length")
                    if count > 2:
                        break
                    page.wait_for_timeout(1500)
                page.wait_for_timeout(5000)

                # Click en el i-esimo boton activo
                clicked = False
                for _ in range(3):
                    result = page.evaluate(f"""() => {{
                        const botones = Array.from(document.querySelectorAll('button')).filter(
                            b => b.textContent.trim().toUpperCase() === 'COMPRAR' && !b.disabled
                        );
                        if (botones[{i}]) {{
                            botones[{i}].scrollIntoView();
                            botones[{i}].click();
                            return true;
                        }}
                        return false;
                    }}""")
                    if result:
                        clicked = True
                        break
                    page.wait_for_timeout(2000)

                if not clicked:
                    continue

                # Esperar navegacion a ticketera
                try:
                    page.wait_for_url("**/ticketera/**", timeout=15000)
                    url_ticketera = page.url
                except Exception:
                    continue

                # Si ya notificamos este partido hoy, saltar
                if ya_notificado(url_ticketera):
                    print(f"Partido {i+1} ya notificado hoy, saltando")
                    continue

                # Esperar que carguen las ubicaciones
                page.wait_for_timeout(4000)
                texto = page.inner_text("body")

                # Extraer nombre del partido
                nombre_partido = ""
                for linea in texto.split("\n"):
                    linea = linea.strip()
                    if "VS" in linea.upper() and "RIVER" in linea.upper() and len(linea) < 60:
                        nombre_partido = linea
                        break

                if UBICACION_OBJETIVO in texto:
                    print(f"Centenario Baja disponible en: {nombre_partido}")
                    mensaje = (
                        f"ENTRADAS DISPONIBLES - CENTENARIO BAJA\n"
                        f"Partido: {nombre_partido}\n"
                        f"Compra ahora: {url_ticketera}"
                    )
                    enviar_whatsapp(mensaje)
                    marcar_notificado(url_ticketera)
                    estado["estado"] = f"CENTENARIO BAJA DISPONIBLE - {nombre_partido}"
                else:
                    print(f"Partido {i+1} ({nombre_partido}): sin Centenario Baja")
                    estado["estado"] = "Hay entradas pero no para Centenario Baja"

        except Exception as e:
            print(f"Error: {e}")
            estado["estado"] = f"Error: {str(e)[:80]}"
        finally:
            browser.close()


def loop_bot():
    print("BOTriver iniciado.")
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
