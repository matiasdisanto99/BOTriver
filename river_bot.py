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
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--disable-extensions",
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        try:
            # LOGIN - sin bloquear nada
            print("🔐 Cargando página de login...")
            page.goto(LOGIN_URL, timeout=40000, wait_until="domcontentloaded")
            page.wait_for_selector("input[name='Email']", timeout=20000)
            print("✅ Formulario de login cargado")

            page.fill("input[name='Email']", RIVER_EMAIL)
            page.fill("input[name='Password']", RIVER_PASSWORD)
            page.click("button[type='submit']")
            page.wait_for_load_state("domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)

            if "Login" in page.url:
                print(f"❌ Login fallido. URL: {page.url}")
                estado["estado"] = "Error de login"
                return

            print(f"✅ Login exitoso. URL: {page.url}")

            # CALENDARIO - bloquear imágenes para ahorrar memoria
            def bloquear_media(route):
                if route.request.resource_type in ["image", "media", "font"]:
                    route.abort()
                else:
                    route.continue_()

            page.route("**/*", bloquear_media)

            print("📅 Cargando calendario...")
            page.goto(CALENDARIO_URL, timeout=40000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            texto = page.inner_text("body").lower()
            print(f"📄 Primeros 300 chars: {texto[:300]}")

            # Buscar botones COMPRAR activos
            botones = page.locator("button, a").all()
            print(f"🔎 Total elementos interactivos: {len(botones)}")

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
                    print(f"🟢 COMPRAR activo encontrado. href: {href}")
                    partidos_activos.append({"href": href})
                except Exception:
                    continue

            if not partidos_activos:
                print("⚪ Sin entradas disponibles.")
                estado["estado"] = "Sin entradas disponibles"
                return

            estado["estado"] = f"{len(partidos_activos)} partido(s) con entradas"

            for partido in partidos_activos:
                href = partido["href"]
                if not href:
                    continue
                url_completa = "https://www.riverid.com.ar" + href if href.startswith("/") else href

                print(f"🔎 Verificando Centenario Baja en: {url_completa}")
                page.goto(url_completa, timeout=40000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

                texto_partido = page.inner_text("body").lower()

                if UBICACION_OBJETIVO not in texto_partido:
                    print("⚪ Centenario Baja no encontrado")
                    continue

                disponible = page.evaluate(f"""() => {{
                    const all = document.querySelectorAll('*');
                    for (const el of all) {{
                        if (el.children.length > 0) continue;
                        if (el.textContent.trim().toLowerCase() === '{UBICACION_OBJETIVO}') {{
                            let p = el;
                            for (let i = 0; i < 8; i++) {{
                                p = p.parentElement;
                                if (!p) break;
                                for (const b of p.querySelectorAll('button, a')) {{
                                    const t = b.textContent.trim().toUpperCase();
                                    if ((t.includes('COMPRAR') || t.includes('SELECCIONAR'))
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
                    mensaje = f"🔴 ENTRADAS DISPONIBLES - CENTENARIO BAJA\nComprá ahora: {CALENDARIO_URL}"
                    enviar_whatsapp(mensaje)
                    estado["estado"] = "✅ ENTRADAS DISPONIBLES - CENTENARIO BAJA"
                else:
                    print("⚪ Centenario Baja no disponible en este partido")

        except Exception as e:
            print(f"❌ Error: {e}")
            estado["estado"] = f"Error: {str(e)[:120]}"
        finally:
            browser.close()


def loop_bot():
    print("🤖 BOTriver iniciado.")
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
