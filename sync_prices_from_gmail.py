"""
sync_prices_from_gmail.py
Bridge: Gmail ➔ Excel ➔ Backend Carlos Isla (/products/sync)
Carlos Isla y Cía. — Portal Clientes

Flujo:
    1. Busca en Gmail el email con asunto configurable ("Portal Clientes - Precios")
       que NO contenga la etiqueta de procesado ("PORTAL_PRECIOS_ACTUALIZADOS").
    2. Extrae el archivo adjunto (.xlsx o .xls).
    3. Envía el archivo por POST multipart/form-data al backend Carlos Isla con x-api-key.
    4. Si el backend responde éxito (HTTP 200), marca el email con el label
       PORTAL_PRECIOS_ACTUALIZADOS y lo marca como leído (evitando reprocesamientos).
"""

import os
import sys
import email
from email.header import decode_header
import base64
import tempfile
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sync_prices")

# Variables principales de conexión con el Backend
BACKEND_SYNC_URL = os.environ.get("BACKEND_SYNC_URL")
EXTERNAL_SYNC_API_KEY = os.environ.get("EXTERNAL_SYNC_API_KEY")

# Asunto y Etiqueta configurables
ASUNTO_EMAIL = os.environ.get("GMAIL_SUBJECT_FILTER", "Portal Clientes - Precios")
LABEL_PROCESADO = os.environ.get("GMAIL_LABEL_PROCESADO", "PORTAL_PRECIOS_ACTUALIZADOS")
GMAIL_SEARCH_QUERY = os.environ.get("GMAIL_SEARCH_QUERY", "").strip()

# Credenciales OAuth (Google API v1 - como en NET-LogistK)
GMAIL_CREDENTIALS_PATH = os.environ.get("GMAIL_CREDENTIALS_PATH", "credentials.json")
GMAIL_TOKEN_PATH = os.environ.get(
    "GMAIL_TOKEN_PATH",
    "token_ventas.json" if os.path.exists("token_ventas.json") else "token.json"
)

# Credenciales IMAP alternativas (App Password)
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")


def decode_mime_words(s):
    if not s:
        return ""
    decoded_fragments = decode_header(s)
    fragments = []
    for fragment, encoding in decoded_fragments:
        if isinstance(fragment, bytes):
            fragments.append(fragment.decode(encoding or "utf-8", errors="replace"))
        else:
            fragments.append(str(fragment))
    return "".join(fragments)


def enviar_al_backend(filename: str, file_bytes: bytes) -> bool:
    """Envía el archivo Excel al endpoint de sincronización del Backend."""
    if not BACKEND_SYNC_URL or not EXTERNAL_SYNC_API_KEY:
        log.error("Faltan BACKEND_SYNC_URL o EXTERNAL_SYNC_API_KEY en las variables de entorno.")
        return False

    log.info(f"Enviando '{filename}' ({len(file_bytes) / 1024:.1f} KB) a {BACKEND_SYNC_URL}...")
    files = {
        "priceList": (
            filename,
            file_bytes,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    headers = {
        "x-api-key": EXTERNAL_SYNC_API_KEY
    }

    try:
        response = requests.post(BACKEND_SYNC_URL, files=files, headers=headers, timeout=180)
        log.info(f"Respuesta HTTP del Backend: {response.status_code}")

        if response.status_code == 200 or response.status_code == 201:
            data = response.json()
            if data.get("success"):
                log.info(f"✓ ÉXITO: {data.get('message', 'Sincronización procesada correctamente')}")
                log.info(f"  Artículos procesados: {data.get('processedCount', 0)}")
                return True
            else:
                log.warning(f"⚠ Finalizó con observaciones: {data.get('message')}")
                return True
        else:
            log.error(f"✗ ERROR del Backend ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        log.error(f"✗ Excepción al conectar con el Backend: {e}")
        return False


# ==============================================================================
# MOTOR 1: Google API v1 con OAuth (Idéntico a NET-LogistK)
# ==============================================================================
def ejecutar_con_google_api() -> bool:
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        log.warning("Librerías de Google API no disponibles para modo OAuth.")
        return False

    if not os.path.exists(GMAIL_TOKEN_PATH):
        log.info(f"Archivo de token OAuth '{GMAIL_TOKEN_PATH}' no encontrado. Se omitirá modo OAuth.")
        return False

    log.info(f"Iniciando conexión con Google API (OAuth) usando {GMAIL_TOKEN_PATH}...")
    SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
    creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.info("Token expirado ➔ Refrescando token OAuth...")
            creds.refresh(Request())
            with open(GMAIL_TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
            with open("TOKEN_REFRESHED", "w") as f:
                f.write("1")
        else:
            log.error("Token OAuth inválido y sin refresh token.")
            return False

    service = build("gmail", "v1", credentials=creds)

    query = GMAIL_SEARCH_QUERY or f'subject:"{ASUNTO_EMAIL}" -label:{LABEL_PROCESADO} has:attachment'
    log.info(f"Buscando correo con query: {query}")
    result = service.users().messages().list(userId="me", q=query, maxResults=1).execute()
    msgs = result.get("messages", [])

    if not msgs:
        log.info(f"No hay correos pendientes con asunto '{ASUNTO_EMAIL}' sin la etiqueta '{LABEL_PROCESADO}'.")
        return True

    msg_id = msgs[0]["id"]
    log.info(f"Correo encontrado (ID: {msg_id}). Obteniendo adjunto...")

    mensaje = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    payload = mensaje.get("payload", {})

    def extraer_adjunto(partes):
        for parte in partes:
            nombre = parte.get("filename", "")
            if nombre.lower().endswith((".xlsx", ".xls")):
                att_id = parte.get("body", {}).get("attachmentId")
                if att_id:
                    raw = service.users().messages().attachments().get(
                        userId="me", messageId=msg_id, id=att_id
                    ).execute()
                    data = base64.urlsafe_b64decode(raw["data"])
                    return nombre, data

            subpartes = parte.get("parts", [])
            if subpartes:
                res = extraer_adjunto(subpartes)
                if res:
                    return res
        return None, None

    target_filename, target_data = extraer_adjunto(payload.get("parts", [payload]))

    if not target_filename or not target_data:
        log.warning("No se encontró ningún adjunto .xlsx/.xls en el correo.")
        return False

    # Enviar al backend
    exito = enviar_al_backend(target_filename, target_data)

    if exito:
        # Marcar con etiqueta de procesado
        try:
            labels = service.users().labels().list(userId="me").execute().get("labels", [])
            label_id = next((l["id"] for l in labels if l["name"] == LABEL_PROCESADO), None)
            if not label_id:
                nuevo = service.users().labels().create(
                    userId="me",
                    body={"name": LABEL_PROCESADO, "labelListVisibility": "labelShow"},
                ).execute()
                label_id = nuevo["id"]
                log.info(f"Etiqueta creada en Gmail: {LABEL_PROCESADO}")

            service.users().messages().modify(
                userId="me",
                id=msg_id,
                body={"addLabelIds": [label_id], "removeLabelIds": ["UNREAD"]},
            ).execute()
            log.info(f"✓ Correo marcado con la etiqueta '{LABEL_PROCESADO}' y como leído.")
        except Exception as e:
            log.warning(f"No se pudo aplicar la etiqueta de procesado en Gmail: {e}")

    return exito


# ==============================================================================
# MOTOR 2: IMAP SSL (con App Password de Gmail)
# ==============================================================================
def ejecutar_con_imap() -> bool:
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        log.error("Modo IMAP: Faltan GMAIL_USER o GMAIL_APP_PASSWORD.")
        return False

    import imaplib

    log.info(f"Conectando por IMAP SSL a Gmail ({GMAIL_USER})...")
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    mail.select("INBOX")

    query = GMAIL_SEARCH_QUERY or f'X-GM-RAW "subject:\"{ASUNTO_EMAIL}\" -label:{LABEL_PROCESADO} has:attachment"'
    log.info(f"Buscando por IMAP con query: {query}")
    status, messages = mail.search(None, query)
    mail_ids = messages[0].split()

    if not mail_ids:
        # Fallback de búsqueda si X-GM-RAW no coincide
        status, messages = mail.search(None, f'(SUBJECT "{ASUNTO_EMAIL}" UNSEEN)')
        mail_ids = messages[0].split()

    if not mail_ids:
        log.info(f"No hay correos pendientes con asunto '{ASUNTO_EMAIL}' sin la etiqueta '{LABEL_PROCESADO}'.")
        mail.close()
        mail.logout()
        return True

    # Tomar el correo más reciente
    target_id = mail_ids[-1]
    res, msg_data = mail.fetch(target_id, "(RFC822)")
    mail_message = None

    for response_part in msg_data:
        if isinstance(response_part, tuple):
            mail_message = email.message_from_bytes(response_part[1])
            break

    if not mail_message:
        log.error("No se pudo leer el contenido del correo.")
        mail.close()
        mail.logout()
        return False

    subject = decode_mime_words(mail_message.get("Subject", ""))
    sender = decode_mime_words(mail_message.get("From", ""))
    log.info(f"Correo seleccionado: '{subject}' de {sender}")

    target_data = None
    target_filename = None

    for part in mail_message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get("Content-Disposition") is None:
            continue

        filename = part.get_filename()
        if filename:
            filename = decode_mime_words(filename)
            if filename.lower().endswith((".xlsx", ".xls")):
                target_filename = filename
                target_data = part.get_payload(decode=True)
                break

    if not target_filename or not target_data:
        log.warning("No se encontró adjunto Excel (.xlsx/.xls) en el mensaje.")
        mail.close()
        mail.logout()
        return False

    exito = enviar_al_backend(target_filename, target_data)

    if exito:
        try:
            # En Gmail IMAP, +X-GM-LABELS agrega la etiqueta especificada
            mail.store(target_id, "+X-GM-LABELS", f"({LABEL_PROCESADO})")
            mail.store(target_id, "+FLAGS", "\\Seen")
            log.info(f"✓ Correo marcado con la etiqueta '{LABEL_PROCESADO}' y como leído.")
        except Exception as e:
            log.warning(f"No se pudo aplicar la etiqueta por IMAP: {e}")

    mail.close()
    mail.logout()
    return exito


def main():
    if not BACKEND_SYNC_URL or not EXTERNAL_SYNC_API_KEY:
        log.error("ERROR: Variables de entorno BACKEND_SYNC_URL y EXTERNAL_SYNC_API_KEY son obligatorias.")
        sys.exit(1)

    # 1. Si existe archivo de token OAuth (estilo NET-LogistK), intentar vía Google API
    if os.path.exists(GMAIL_TOKEN_PATH) or os.getenv("GMAIL_TOKEN_JSON"):
        if not os.path.exists(GMAIL_TOKEN_PATH) and os.getenv("GMAIL_TOKEN_JSON"):
            with open(GMAIL_TOKEN_PATH, "w") as f:
                f.write(os.getenv("GMAIL_TOKEN_JSON"))

        log.info("Modo de conexión: Google API v1 (OAuth)")
        exito = ejecutar_con_google_api()
        sys.exit(0 if exito else 1)

    # 2. Si no, conectar vía IMAP con App Password
    elif GMAIL_USER and GMAIL_APP_PASSWORD:
        log.info("Modo de conexión: Gmail IMAP SSL (App Password)")
        exito = ejecutar_con_imap()
        sys.exit(0 if exito else 1)

    else:
        log.error(
            "ERROR: No se configuraron credenciales válidas de Gmail.\n"
            "Debe configurar GMAIL_USER y GMAIL_APP_PASSWORD (modo IMAP)\n"
            "O bien GMAIL_CREDENTIALS_JSON y GMAIL_TOKEN_JSON (modo OAuth NET-LogistK)."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
