"""
sync_prices_from_gmail.py
Bridge: Gmail ➔ Excel ➔ Backend Carlos Isla (/products/sync)
Carlos Isla y Cía. — Portal Clientes

Soporta múltiples entornos (QA, PROD o AMBOS):
    - QA: Envía a BACKEND_SYNC_URL_QA
    - PROD: Envía a BACKEND_SYNC_URL_PROD
    - AMBOS: Envía a QA y luego a PROD con el mismo archivo
"""

import os
import sys
import email
from email.header import decode_header
import base64
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sync_prices")

# Selección de entorno objetivo: "QA", "PROD" o "AMBOS"
TARGET_ENV = os.environ.get("TARGET_ENV", "AMBOS").upper().strip()

# Configuración QA
QA_URL = os.environ.get("BACKEND_SYNC_URL_QA") or os.environ.get("BACKEND_SYNC_URL") or "https://qa-api.clientes.carlosisla.com.ar/products/sync"
QA_KEY = os.environ.get("EXTERNAL_SYNC_API_KEY_QA") or os.environ.get("EXTERNAL_SYNC_API_KEY")

# Configuración Producción
PROD_URL = os.environ.get("BACKEND_SYNC_URL_PROD") or os.environ.get("BACKEND_SYNC_URL") or "https://api-clientes.carlosisla.com.ar/products/sync"
PROD_KEY = os.environ.get("EXTERNAL_SYNC_API_KEY_PROD") or os.environ.get("EXTERNAL_SYNC_API_KEY")

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


def _loguear_errores_backend(env_name: str, data: dict, maximo: int = 5) -> None:
    """Muestra en el log las primeras filas con error que devolvió el backend."""
    errores = data.get("errors") or []
    for err in errores[:maximo]:
        log.warning(
            f"[{env_name}]   Fila {err.get('row')} código '{err.get('code')}' "
            f"({err.get('field')}): {err.get('message')}"
        )
    if len(errores) > maximo:
        log.warning(f"[{env_name}]   ... y {len(errores) - maximo} observaciones más.")


def enviar_a_backend(url: str, api_key: str, env_name: str, filename: str, file_bytes: bytes) -> bool:
    """Envía el archivo Excel a un backend específico."""
    if not url or not api_key:
        log.error(f"[{env_name}] Faltan URL o API Key en las variables de entorno.")
        return False

    log.info(f"[{env_name}] Enviando '{filename}' ({len(file_bytes) / 1024:.1f} KB) a {url}...")
    files = {
        "priceList": (
            filename,
            file_bytes,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    headers = {
        "x-api-key": api_key
    }

    try:
        response = requests.post(url, files=files, headers=headers, timeout=180)
        log.info(f"[{env_name}] Respuesta HTTP: {response.status_code}")

        if response.status_code in [200, 201]:
            data = response.json()
            procesados = data.get("processedCount", 0) or 0

            # Un HTTP 200 sin artículos procesados no es un éxito: si se diera por bueno,
            # el correo quedaría etiquetado como procesado y nunca se reintentaría.
            if procesados == 0:
                log.error(f"[{env_name}] ✗ El backend no procesó ningún artículo: {data.get('message')}")
                _loguear_errores_backend(env_name, data)
                return False

            if data.get("success"):
                log.info(f"[{env_name}] ✓ ÉXITO: {data.get('message', 'Sincronización procesada correctamente')}")
                log.info(f"[{env_name}]   Artículos procesados: {procesados}")
            else:
                log.warning(f"[{env_name}] ⚠ Finalizó con observaciones: {data.get('message')}")
                log.warning(f"[{env_name}]   Artículos procesados: {procesados}")
                _loguear_errores_backend(env_name, data)

            # El backend ya no crea ni quita artículos: informa las diferencias para que decida el admin.
            dif = data.get("differences") or {}
            nuevos, ausentes = dif.get("newInFile", 0), dif.get("missingInFile", 0)
            if nuevos or ausentes:
                log.warning(
                    f"[{env_name}]   Diferencias para el administrador: {nuevos} códigos del Excel no existen "
                    f"en el portal y {ausentes} artículos activos no están en el Excel (revisar en el portal)."
                )
            return True
        else:
            log.error(f"[{env_name}] ✗ ERROR ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        log.error(f"[{env_name}] ✗ Excepción al conectar: {e}")
        return False


def sincronizar_en_entornos(filename: str, file_bytes: bytes) -> bool:
    """Sincroniza según TARGET_ENV ('QA', 'PROD' o 'AMBOS')."""
    destinos = []
    if TARGET_ENV in ["QA", "AMBOS"]:
        destinos.append(("QA", QA_URL, QA_KEY))
    if TARGET_ENV in ["PROD", "AMBOS"]:
        destinos.append(("PRODUCCIÓN", PROD_URL, PROD_KEY))

    if not destinos:
        log.error(f"Valor TARGET_ENV inválido: '{TARGET_ENV}'. Debe ser 'QA', 'PROD' o 'AMBOS'.")
        return False

    todos_ok = True
    for env_name, url, key in destinos:
        ok = enviar_a_backend(url, key, env_name, filename, file_bytes)
        if not ok:
            todos_ok = False

    return todos_ok


# ==============================================================================
# MOTOR 1: Google API v1 con OAuth (Idéntico a NET-LogistK)
# ==============================================================================
def _listar_mensajes_pendientes(service, query: str) -> list:
    """Devuelve todos los correos que cumplen la query, del más reciente al más antiguo."""
    mensajes = []
    page_token = None
    while True:
        result = service.users().messages().list(
            userId="me", q=query, maxResults=500, pageToken=page_token
        ).execute()
        mensajes.extend(result.get("messages", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            return mensajes


def _obtener_o_crear_label(service) -> str:
    """Devuelve el id de la etiqueta de 'procesado', creándola si no existe."""
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    label_id = next((l["id"] for l in labels if l["name"] == LABEL_PROCESADO), None)
    if not label_id:
        nuevo = service.users().labels().create(
            userId="me",
            body={"name": LABEL_PROCESADO, "labelListVisibility": "labelShow"},
        ).execute()
        label_id = nuevo["id"]
        log.info(f"Etiqueta creada en Gmail: {LABEL_PROCESADO}")
    return label_id


def ejecutar_con_google_api() -> bool:
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        log.warning("Librerías de Google API no disponibles para modo OAuth.")
        return False

    if not os.path.exists(GMAIL_TOKEN_PATH):
        log.info(f"Archivo de token OAuth '{GMAIL_TOKEN_PATH}' no encontrado.")
        return False

    log.info(f"Iniciando conexión con Google API (OAuth) usando {GMAIL_TOKEN_PATH}...")
    SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
    creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.info("Token expirado ➔ Refrescando token OAuth...")
            try:
                creds.refresh(Request())
            except RefreshError as e:
                log.error(
                    f"No se pudo refrescar el token OAuth de Gmail: {e}\n"
                    "Si el error es 'invalid_grant', el refresh token venció o fue revocado "
                    "(pasa a los 7 días si la app de Google Cloud está en modo 'Testing'). "
                    "Regenerar token.json y actualizar el secret GMAIL_TOKEN_JSON en GitHub."
                )
                return False
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
    msgs = _listar_mensajes_pendientes(service, query)

    if not msgs:
        log.info(f"No hay correos pendientes con asunto '{ASUNTO_EMAIL}' sin la etiqueta '{LABEL_PROCESADO}'.")
        return True

    # Gmail devuelve primero el más reciente: ese es el que se sincroniza.
    msg_id = msgs[0]["id"]
    ids_anteriores = [m["id"] for m in msgs[1:]]
    log.info(f"Correo encontrado (ID: {msg_id}). Obteniendo adjunto...")
    if ids_anteriores and not GMAIL_SEARCH_QUERY:
        log.info(f"Hay {len(ids_anteriores)} correo(s) anteriores pendientes que se descartarán tras el sync.")

    mensaje = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    payload = mensaje.get("payload", {})

    target_data = None
    target_filename = None

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

    # Enviar al/los backend(s)
    exito = sincronizar_en_entornos(target_filename, target_data)

    if exito:
        # Marcar con etiqueta de procesado
        try:
            label_id = _obtener_o_crear_label(service)

            service.users().messages().modify(
                userId="me",
                id=msg_id,
                body={"addLabelIds": [label_id], "removeLabelIds": ["UNREAD"]},
            ).execute()
            log.info(f"✓ Correo marcado con la etiqueta '{LABEL_PROCESADO}' y como leído.")

            # Los correos más viejos con el mismo asunto traen listas de precios desactualizadas:
            # si quedaran sin etiqueta, las próximas ejecuciones las tomarían de a una y el
            # backend pisaría los precios vigentes. Solo aplica a la búsqueda automática; con una
            # query manual (workflow_dispatch) se respeta exactamente el correo pedido.
            if ids_anteriores and not GMAIL_SEARCH_QUERY:
                for i in range(0, len(ids_anteriores), 1000):
                    service.users().messages().batchModify(
                        userId="me",
                        body={"ids": ids_anteriores[i:i + 1000], "addLabelIds": [label_id]},
                    ).execute()
                log.info(f"✓ {len(ids_anteriores)} correo(s) anteriores marcados como procesados (descartados).")
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
        status, messages = mail.search(None, f'(SUBJECT "{ASUNTO_EMAIL}" UNSEEN)')
        mail_ids = messages[0].split()

    if not mail_ids:
        log.info(f"No hay correos pendientes con asunto '{ASUNTO_EMAIL}' sin la etiqueta '{LABEL_PROCESADO}'.")
        mail.close()
        mail.logout()
        return True

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

    exito = sincronizar_en_entornos(target_filename, target_data)

    if exito:
        try:
            mail.store(target_id, "+X-GM-LABELS", f"({LABEL_PROCESADO})")
            mail.store(target_id, "+FLAGS", "\\Seen")
            log.info(f"✓ Correo marcado con la etiqueta '{LABEL_PROCESADO}' y como leído.")

            # IMAP devuelve los ids en orden ascendente: el último es el más reciente.
            # Los anteriores traen listas viejas y se descartan (ver nota en el motor OAuth).
            ids_anteriores = [i for i in mail_ids if i != target_id]
            if ids_anteriores and not GMAIL_SEARCH_QUERY:
                for mid in ids_anteriores:
                    mail.store(mid, "+X-GM-LABELS", f"({LABEL_PROCESADO})")
                log.info(f"✓ {len(ids_anteriores)} correo(s) anteriores marcados como procesados (descartados).")
        except Exception as e:
            log.warning(f"No se pudo aplicar la etiqueta por IMAP: {e}")

    mail.close()
    mail.logout()
    return exito


def main():
    log.info(f"=== Sincronización de Precios iniciada | Entorno(s): {TARGET_ENV} ===")

    # 1. Si existe archivo de token OAuth, intentar vía Google API
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
