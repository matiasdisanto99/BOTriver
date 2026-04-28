import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from twilio.rest import Client
from playwright.sync_api import sync_playwright

# ─── CONFIGURACIÓN (variables de entorno) ────────────────────
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
# ─────────────────────────────────────────────────────────────

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
        client.messages.create(
            body=mensaje,
            from_=TWILIO_WHATSAPP_FROM,
            to=TWILIO_WHATSAPP_TO
        )
        print(f"✅ WhatsApp enviado: {mensaje}")
    except Exception as e:
        print(f"❌ Error enviando WhatsApp: {e}")


def chequear_entradas():
    estado["ultimo_chequeo"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    print("🔍 Chequeando entradas...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15")
        page = context.new_page()

        try:
            # Login
            print("🔐 Haciendo login...")
            page.goto(LOGIN_URL, timeout=20000)
            page.wait_for_load_state("networkidle", timeout=15000)

            page.fill("input[name='Email']", RIVER_EMAIL)
            page.fill("input[name='Password']", RIVER_PASSWORD)
            page.click("button[type='submit'], input[type='submit']")
            page.wait_for_load_state("networkidle", timeout=15000)

            if "Login" in page.url:
                print(f"❌ Login fallido. URL: {page.url}")
                estado["estado"] = "Error de login"
                return

            print("✅ Login exitoso")

            # Ir al calendario
            page.goto(CALENDARIO_URL, timeout=20000)
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_timeout(2000)

            # Buscar botones COMPRAR activos
            botones = page.query_selector_all("button, a")
            partidos_activos = []

            for boton in botones:
                try:
                    texto = (boton.inner_text() or "").strip().upper()
                    if texto != "COMPRAR":
                        continue
                    if not boton.is_visible() or not boton.is_enabled():
                        continue
                    clases = boton.get_attribute("class") or ""
                    disabled = boton.get_attribute("disabled")
                    if disabled or "disabled" in clases.lower():
                        continue

                    href = boton.get_attribute("href") or ""
                    if not href:
                        parent = page.evaluate("el => el.closest('a')?.href || ''", boton)
                        href = parent or ""

                    # Nombre del partido
                    try:
                        nombre = page.evaluate("""el => {
                            let p = el;
                            for(let i = 0; i < 6; i++) {
                                p = p.parentElement;
                                if(!p) break;
                                if(p.textContent.length > 30) return p.textContent.trim().substring(0, 80);
                            }
                            return 'Partido sin nombre';
                        }""", boton)
                    except Exception:
                        nombre = "Partido sin nombre"

                    partidos_activos.append({"nombre": nombre, "href": href})
                except Exception:
                    continue

            if not partidos_activos:
                print("⚪ No hay partidos con entradas disponibles.")
                estado["estado"] = "Sin entradas disponibles"
                return

            print(f"🟢 {len(partidos_activos)} partido(s) con entradas. Verificando Centenario Baja...")
            estado["estado"] = f"{len(partidos_activos)} partido(s) con entradas activas"

            for partido in partidos_activos:
                nombre = partido["nombre"]
                href = partido["href"]

                if not href:
                    continue

                # Verificar Centenario Baja en el partido
                print(f"🔎 Verificando: {nombre[:50]}")
                page.goto(href, timeout=20000)
                page.wait_for_load_state("networkidle", timeout=15000)
                page.wait_for_timeout(2000)

                texto_pagina = page.inner_text("body").lower()

                if UBICACION_OBJETIVO not in texto_pagina:
                    print(f"⚪ '{UBICACION_OBJETIVO}' no encontrado en: {nombre[:40]}")
                    continue

                # Buscar si el botón de Centenario Baja está activo
                elementos_ubi = page.query_selector_all(f"*")
                centenario_disponible = False

                for el in elementos_ubi:
                    try:
                        texto_el = (el.inner_text() or "").lower()
                        if UBICACION_OBJETIVO not in texto_el or len(texto_el) > 100:
                            continue
                        # Buscar botón comprar cercano
                        boton_cercano = page.evaluate("""el => {
                            let p = el;
                            for(let i = 0; i < 6; i++) {
                                p = p.parentElement;
                                if(!p) break;
                                let btns = p.querySelectorAll('button, a');
                                for(let b of btns) {
                                    let t = b.textContent.trim().toUpperCase();
                                    if((t.includes('COMPRAR') || t.includes('SELECCIONAR')) && !b.disabled && !b.className.includes('disabled')) {
                                        return true;
                                    }
                                }
                            }
                            return false;
                        }""", el)
                        if boton_cercano:
                            centenario_disponible = True
                            break
                    except Exception:
                        continue

                if centenario_disponible:
                    mensaje = (
                        f"🔴 ENTRADAS DISPONIBLES - CENTENARIO BAJA\n"
                        f"Partido: {nombre[:60]}\n"
                        f"Comprá ahora: {CALENDARIO_URL}"
                    )
                    enviar_whatsapp(mensaje)
                    estado["estado"] = f"✅ ENTRADAS DISPONIBLES - {nombre[:40]}"
                else:
                    print(f"⚪ Centenario Baja no disponible para: {nombre[:50]}")

        except Exception as e:
            print(f"❌ Error en chequeo: {e}")
            estado["estado"] = f"Error: {str(e)[:60]}"
        finally:
            browser.close()


def loop_bot():
    print("🤖 Bot River con Playwright iniciado.")
    enviar_whatsapp("🤖 Bot iniciado correctamente. Te aviso cuando haya Centenario Baja disponible.")
    while True:
        try:
            chequear_entradas()
        except Exception as e:
            print(f"❌ Error en loop: {e}")
        print(f"⏳ Esperando {INTERVALO_MINUTOS} minutos...")
        time.sleep(INTERVALO_MINUTOS * 60)


if __name__ == "__main__":
    hilo_bot = threading.Thread(target=loop_bot, daemon=True)
    hilo_bot.start()
    print("🌐 Servidor web iniciado")
    iniciar_servidor()
