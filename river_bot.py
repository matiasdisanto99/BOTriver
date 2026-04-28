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
    print(f"🔍 Chequeando entradas... {estado['ultimo_chequeo']}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-images",
                "--blink-settings=imagesEnabled=false",
                "--disable-extensions",
                "--disable-plugins",
                "--memory-pressure-off",
                "--single-process",
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            java_script_enabled=True,
        )

        # Bloquear recursos innecesarios para ahorrar memoria
        def bloquear_recursos(route):
            if route.request.resource_type in ["image", "media", "font", "stylesheet"]:
                route.abort()
            else:
                route.continue_()

        page = context.new_page()
        page.route("**/*", bloquear_recursos)

        try:
            print("🔐 Haciendo login...")
            page.goto(LOGIN_URL, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

            page.fill("input[name='Email']", RIVER_EMAIL)
            page.fill("input[name='Password']", RIVER_PASSWORD)
            page.click("button[type='submit'], input[type='submit']")
            page.wait_for_load_state("domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)

            if "Login" in page.url:
                print(f"❌ Login fallido. URL: {page.url}")
                estado["estado"] = "Error de login"
                return

            print(f"✅ Login exitoso. URL: {page.url}")

            print("📅 Cargando calendario...")
            page.goto(CALENDARIO_URL, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            html = page.content()
            texto = page.inner_text("body").lower()
            print(f"📄 Texto de la página (primeros 300 chars): {texto[:300]}")

            # Buscar botones COMPRAR activos
            botones = page.locator("button, a").all()
            print(f"🔎 Total botones/links encontrados: {len(botones)}")

            partidos_activos = []
            for boton in botones:
                try:
                    texto_boton = (boton.inner_text() or "").strip().upper()
                    if "COMPRAR" not in texto_boton:
                        continue
                    if not boton.is_visible() or not boton.is_enabled():
                        continue
                    clases = boton.get_attribute("class") or ""
                    disabled = boton.get_attribute("disabled")
                    if disabled or "disabled" in clases.lower():
                        continue

                    href = boton.get_attribute("href") or ""
                    print(f"🟢 Botón COMPRAR activo encontrado. href: {href}")
                    partidos_activos.append({"href": href})
                except Exception:
                    continue

            if not partidos_activos:
                print("⚪ No hay partidos con entradas disponibles.")
                estado["estado"] = "Sin entradas disponibles"
                return

            print(f"🟢 {len(partidos_activos)} partido(s) con entradas. Verificando Centenario Baja...")
            estado["estado"] = f"{len(partidos_activos)} partido(s) con entradas activas"

            for partido in partidos_activos:
                href = partido["href"]
                if not href:
                    continue

                if href.startswith("/"):
                    url_completa = "https://www.riverid.com.ar" + href
                else:
                    url_completa = href

                print(f"🔎 Verificando Centenario Baja en: {url_completa}")
                page.goto(url_completa, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                texto_partido = page.inner_text("body").lower()
                print(f"📄 Texto partido (primeros 200 chars): {texto_partido[:200]}")

                if UBICACION_OBJETIVO not in texto_partido:
                    print(f"⚪ Centenario Baja no encontrado en este partido")
                    continue

                # Verificar si hay botón activo cerca de Centenario Baja
                disponible = page.evaluate(f"""() => {{
                    const elementos = document.querySelectorAll('*');
                    for (const el of elementos) {{
                        if (el.children.length > 0) continue;
                        const texto = el.textContent.trim().toLowerCase();
                        if (texto === '{UBICACION_OBJETIVO}') {{
                            let padre = el;
                            for (let i = 0; i < 8; i++) {{
                                padre = padre.parentElement;
                                if (!padre) break;
                                const botones = padre.querySelectorAll('button, a');
                                for (const b of botones) {{
                                    const t = b.textContent.trim().toUpperCase();
                                    if ((t.includes('COMPRAR') || t.includes('SELECCIONAR')) 
                                        && !b.disabled 
                                        && !b.className.includes('disabled')) {{
                                        return true;
                                    }}
                                }}
                            }}
                        }}
                    }}
                    return false;
                }}""")

                if disponible:
                    mensaje = (
                        f"🔴 ENTRADAS DISPONIBLES - CENTENARIO BAJA\n"
                        f"Comprá ahora: {CALENDARIO_URL}"
                    )
                    enviar_whatsapp(mensaje)
                    estado["estado"] = "✅ ENTRADAS DISPONIBLES - CENTENARIO BAJA"
                else:
                    print(f"⚪ Centenario Baja encontrado pero no disponible")

        except Exception as e:
            print(f"❌ Error en chequeo: {e}")
            estado["estado"] = f"Error: {str(e)[:80]}"
        finally:
            browser.close()


def loop_bot():
    print("🤖 BOTriver iniciado con Playwright optimizado.")
    enviar_whatsapp("🤖 Bot iniciado. Te aviso cuando haya Centenario Baja disponible.")
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
