import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from twilio.rest import Client

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


def crear_driver():
    opciones = Options()
    opciones.add_argument("--headless")
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--disable-gpu")
    opciones.add_argument("--window-size=1920,1080")
    opciones.add_argument("user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15")
    driver = webdriver.Chrome(options=opciones)
    return driver


def hacer_login(driver):
    try:
        driver.get(LOGIN_URL)
        wait = WebDriverWait(driver, 15)

        campo_email = wait.until(EC.presence_of_element_located((By.NAME, "Email")))
        campo_email.send_keys(RIVER_EMAIL)

        campo_pass = driver.find_element(By.NAME, "Password")
        campo_pass.send_keys(RIVER_PASSWORD)

        boton = driver.find_element(By.CSS_SELECTOR, "button[type='submit'], input[type='submit']")
        boton.click()

        time.sleep(3)

        if "Login" not in driver.current_url:
            print("✅ Login exitoso")
            return True
        else:
            print(f"❌ Login fallido. URL: {driver.current_url}")
            return False
    except Exception as e:
        print(f"❌ Error en login: {e}")
        return False


def verificar_centenario_baja(driver, url_partido):
    try:
        driver.get(url_partido)
        time.sleep(3)

        texto_pagina = driver.find_element(By.TAG_NAME, "body").text.lower()

        if UBICACION_OBJETIVO not in texto_pagina:
            print(f"⚪ '{UBICACION_OBJETIVO}' no encontrado en la página del partido")
            return False

        # Buscar el elemento de Centenario Baja
        elementos = driver.find_elements(By.XPATH, f"//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{UBICACION_OBJETIVO}')]")

        for elemento in elementos:
            try:
                # Buscar botón de comprar cercano
                parent = elemento.find_element(By.XPATH, "./ancestor::*[position()<=5]")
                botones = parent.find_elements(By.XPATH, ".//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'comprar') or contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'seleccionar')]")
                for boton in botones:
                    if boton.is_enabled() and boton.is_displayed():
                        clases = boton.get_attribute("class") or ""
                        disabled = boton.get_attribute("disabled")
                        if not disabled and "disabled" not in clases.lower():
                            print(f"✅ Centenario Baja disponible!")
                            return True
            except Exception:
                continue

        return False
    except Exception as e:
        print(f"❌ Error verificando Centenario Baja: {e}")
        return False


def chequear_entradas():
    estado["ultimo_chequeo"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    print("🔍 Chequeando entradas...")

    driver = None
    try:
        driver = crear_driver()

        if not hacer_login(driver):
            estado["estado"] = "Error de login"
            return

        driver.get(CALENDARIO_URL)
        time.sleep(4)

        # Buscar botones COMPRAR activos (rojos)
        botones_comprar = driver.find_elements(By.XPATH, "//button[contains(translate(text(), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'COMPRAR')] | //a[contains(translate(text(), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'COMPRAR')]")

        partidos_activos = []
        for boton in botones_comprar:
            try:
                if not boton.is_enabled() or not boton.is_displayed():
                    continue
                clases = boton.get_attribute("class") or ""
                disabled = boton.get_attribute("disabled")
                if disabled or "disabled" in clases.lower() or "gris" in clases.lower():
                    continue

                href = boton.get_attribute("href") or ""
                if not href:
                    try:
                        parent_a = boton.find_element(By.XPATH, "./ancestor::a")
                        href = parent_a.get_attribute("href") or ""
                    except Exception:
                        pass

                # Obtener nombre del partido
                try:
                    card = boton.find_element(By.XPATH, "./ancestor::div[contains(@class, 'partido') or contains(@class, 'card') or contains(@class, 'match')][1]")
                    nombre = card.text[:80]
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

            disponible = verificar_centenario_baja(driver, href)

            if disponible:
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
        print(f"❌ Error inesperado en chequeo: {e}")
        estado["estado"] = f"Error: {str(e)[:50]}"
    finally:
        if driver:
            driver.quit()


def loop_bot():
    print("🤖 Bot River iniciado con Selenium. Chequeando cada 10 minutos.")
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
