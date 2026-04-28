import requests
from bs4 import BeautifulSoup
from twilio.rest import Client
import time
import os

# ─── CONFIGURACIÓN (todas desde variables de entorno) ────────
RIVER_EMAIL = os.environ["RIVER_EMAIL"]
RIVER_PASSWORD = os.environ["RIVER_PASSWORD"]
TWILIO_SID = os.environ["TWILIO_SID"]
TWILIO_TOKEN = os.environ["TWILIO_TOKEN"]
TWILIO_WHATSAPP_FROM = os.environ["TWILIO_WHATSAPP_FROM"]
TWILIO_WHATSAPP_TO = os.environ["TWILIO_WHATSAPP_TO"]

LOGIN_URL = "https://login.riverid.com.ar/Account/Login"
PARTIDOS_URL = "https://www.riverid.com.ar/Tickets/ProximosPartidos"
UBICACION_OBJETIVO = "centenario baja"
INTERVALO_MINUTOS = 10
# ─────────────────────────────────────────────────────────────


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


def hacer_login(session):
    try:
        response = session.get(LOGIN_URL, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")
        token_input = soup.find("input", {"name": "__RequestVerificationToken"})
        token = token_input["value"] if token_input else ""
        payload = {
            "Email": RIVER_EMAIL,
            "Password": RIVER_PASSWORD,
            "__RequestVerificationToken": token,
            "RememberMe": "false"
        }
        headers = {
            "Referer": LOGIN_URL,
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"
        }
        login_response = session.post(LOGIN_URL, data=payload, headers=headers, allow_redirects=True, timeout=15)
        if "Login" not in login_response.url:
            print("✅ Login exitoso")
            return True
        else:
            print(f"❌ Login fallido. URL: {login_response.url}")
            return False
    except Exception as e:
        print(f"❌ Error en login: {e}")
        return False


def verificar_centenario_baja(session, url_partido):
    try:
        response = session.get(url_partido, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")
        texto = soup.get_text().lower()
        if UBICACION_OBJETIVO not in texto:
            return False
        ubicaciones = soup.find_all(lambda tag: UBICACION_OBJETIVO in tag.get_text().lower())
        for ubi in ubicaciones:
            parent = ubi.find_parent()
            if parent:
                botones = parent.find_all("button") + parent.find_all("a")
                for boton in botones:
                    clases = " ".join(boton.get("class", []))
                    texto_boton = boton.get_text().lower()
                    disabled = boton.get("disabled")
                    if "comprar" in texto_boton or "seleccionar" in texto_boton:
                        if not disabled and "disabled" not in clases:
                            return True
        return False
    except Exception as e:
        print(f"❌ Error verificando ubicación: {e}")
        return False


def chequear_entradas():
    print("🔍 Chequeando entradas...")
    session = requests.Session()
    if not hacer_login(session):
        print("No se pudo hacer login. Reintentando en el próximo ciclo.")
        return
    try:
        response = session.get(PARTIDOS_URL, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        print(f"❌ Error accediendo a próximos partidos: {e}")
        return

    botones_comprar = soup.find_all(
        lambda tag: tag.name in ["a", "button"] and tag.get_text(strip=True).upper() == "COMPRAR"
    )

    partidos_con_entradas = []
    for boton in botones_comprar:
        clases = " ".join(boton.get("class", []))
        if boton.get("disabled") or "disabled" in clases:
            continue
        href = boton.get("href", "")
        if not href:
            parent = boton.find_parent("a")
            if parent:
                href = parent.get("href", "")
        card = boton.find_parent(
            lambda tag: tag.name in ["div", "article", "section"] and len(tag.get_text(strip=True)) > 30
        )
        nombre = card.get_text(separator=" ", strip=True)[:80] if card else "Partido sin nombre"
        partidos_con_entradas.append({"nombre": nombre, "href": href})

    if not partidos_con_entradas:
        print("⚪ No hay partidos con entradas disponibles.")
        return

    print(f"🟢 {len(partidos_con_entradas)} partido(s) con entradas activas. Verificando Centenario Baja...")

    for partido in partidos_con_entradas:
        nombre = partido["nombre"]
        href = partido["href"]
        if href and href.startswith("/"):
            url_completa = "https://www.riverid.com.ar" + href
        elif href and href.startswith("http"):
            url_completa = href
        else:
            url_completa = None

        disponible = verificar_centenario_baja(session, url_completa) if url_completa else False

        if disponible:
            mensaje = (
                f"🔴 ENTRADAS DISPONIBLES - CENTENARIO BAJA\n"
                f"Partido: {nombre[:60]}\n"
                f"Comprá ahora: https://www.riverid.com.ar/Tickets/ProximosPartidos"
            )
            enviar_whatsapp(mensaje)
        else:
            print(f"⚪ Centenario Baja no disponible para: {nombre[:50]}")


if __name__ == "__main__":
    print("🤖 Bot de entradas River iniciado. Chequeando cada 10 minutos.")
    enviar_whatsapp("🤖 Bot iniciado correctamente. Te aviso cuando haya Centenario Baja disponible.")
    while True:
        try:
            chequear_entradas()
        except Exception as e:
            print(f"❌ Error inesperado: {e}")
        print(f"⏳ Esperando {INTERVALO_MINUTOS} minutos...")
        time.sleep(INTERVALO_MINUTOS * 60)
