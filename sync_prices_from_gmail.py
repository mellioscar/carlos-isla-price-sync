"""
sync_prices_from_gmail.py
Bridge: Gmail ➔ Excel ➔ Backend Carlos Isla (/products/sync)
Carlos Isla y Cía. — Portal Clientes

Soporta múltiples entornos (QA, PROD o AMBOS):
    - QA: Envía a BACKEND_SYNC_URL_QA
    - PROD: Envía a BACKEND_SYNC_URL_PROD
    - AMBOS: Envía a QA y luego a PROD

Cada entorno tiene su propia etiqueta de Gmail (PORTAL_PRECIOS_QA / PORTAL_PRECIOS_PROD):
un correo se considera procesado por entorno, no globalmente.
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
# Etiqueta propia por entorno (PORTAL_PRECIOS_ACTUALIZADOS queda como histórica: cuenta como procesado para QA)
LABEL_QA = os.environ.get("GMAIL_LABEL_QA", "PORTAL_PRECIOS_QA")
LABEL_PROD = os.environ.get("GMAIL_LABEL_PROD", "PORTAL_PRECIOS_PROD")
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


def entornos_destino() -> list:
    """
    Entornos a sincronizar según TARGET_ENV, con la etiqueta de Gmail propia de cada uno.

    Cada entorno lleva su propia etiqueta de "procesado": así una corrida solo-QA no deja a PROD
    sin la lista, y una falla en un entorno no impide avanzar al otro.
    `excluir` son etiquetas que también cuentan como "ya procesado" para ese entorno: la etiqueta
    histórica única (LABEL_PROCESADO) solo se aplicó a corridas que siempre incluyeron QA.
    """
    destinos = []
    if TARGET_ENV in ["QA", "AMBOS"]:
        destinos.append({"nombre": "QA", "url": QA_URL, "key": QA_KEY, "label": LABEL_QA, "excluir": [LABEL_QA, LABEL_PROCESADO]})
    if TARGET_ENV in ["PROD", "AMBOS"]:
        destinos.append({"nombre": "PRODUCCIÓN", "url": PROD_URL, "key": PROD_KEY, "label": LABEL_PROD, "excluir": [LABEL_PROD]})
    if not destinos:
        log.error(f"Valor TARGET_ENV inválido: '{TARGET_ENV}'. Debe ser 'QA', 'PROD' o 'AMBOS'.")
    return destinos


def query_para(destino: dict) -> str:
    """Query de Gmail del entorno: correos con el asunto y adjunto que ese entorno todavía no procesó."""
    if GMAIL_SEARCH_QUERY:
        return GMAIL_SEARCH_QUERY  # búsqueda manual: se respeta tal cual
    excluir = " ".join(f"-label:{l}" for l in destino["excluir"])
    return f'subject:"{ASUNTO_EMAIL}" {excluir} has:attachment'


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


def _obtener_o_crear_label(service, nombre: str) -> str:
    """Devuelve el id de la etiqueta indicada, creándola si no existe."""
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    label_id = next((l["id"] for l in labels if l["name"] == nombre), None)
    if not label_id:
        nuevo = service.users().labels().create(
            userId="me",
            body={"name": nombre, "labelListVisibility": "labelShow"},
        ).execute()
        label_id = nuevo["id"]
        log.info(f"Etiqueta creada en Gmail: {nombre}")
    return label_id


def _descargar_adjunto_excel(service, msg_id: str):
    """(nombre, bytes) del primer adjunto .xlsx/.xls del correo, o (None, None)."""
    mensaje = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    payload = mensaje.get("payload", {})

    def extraer(partes):
        for parte in partes:
            nombre = parte.get("filename", "")
            if nombre.lower().endswith((".xlsx", ".xls")):
                att_id = parte.get("body", {}).get("attachmentId")
                if att_id:
                    raw = service.users().messages().attachments().get(
                        userId="me", messageId=msg_id, id=att_id
                    ).execute()
                    return nombre, base64.urlsafe_b64decode(raw["data"])
            subpartes = parte.get("parts", [])
            if subpartes:
                res = extraer(subpartes)
                if res and res[0]:
                    return res
        return None, None

    return extraer(payload.get("parts", [payload]))


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

    destinos = entornos_destino()
    if not destinos:
        return False

    adjuntos = {}  # msg_id ➔ (nombre, bytes): si ambos entornos usan el mismo correo, se descarga una vez
    todos_ok = True

    for destino in destinos:
        env = destino["nombre"]
        query = query_para(destino)
        log.info(f"[{env}] Buscando correo con query: {query}")
        msgs = _listar_mensajes_pendientes(service, query)

        if not msgs:
            log.info(f"[{env}] No hay correos pendientes para este entorno (etiqueta '{destino['label']}').")
            continue

        # Gmail devuelve primero el más reciente: ese es el que se sincroniza.
        msg_id = msgs[0]["id"]
        ids_anteriores = [m["id"] for m in msgs[1:]]
        log.info(f"[{env}] Correo encontrado (ID: {msg_id}).")
        if ids_anteriores and not GMAIL_SEARCH_QUERY:
            log.info(f"[{env}] Hay {len(ids_anteriores)} correo(s) anteriores pendientes que se descartarán tras el sync.")

        if msg_id not in adjuntos:
            adjuntos[msg_id] = _descargar_adjunto_excel(service, msg_id)
        nombre, datos = adjuntos[msg_id]
        if not nombre or not datos:
            log.warning(f"[{env}] No se encontró ningún adjunto .xlsx/.xls en el correo.")
            todos_ok = False
            continue

        if not enviar_a_backend(destino["url"], destino["key"], env, nombre, datos):
            todos_ok = False
            continue

        try:
            label_id = _obtener_o_crear_label(service, destino["label"])
            service.users().messages().modify(
                userId="me",
                id=msg_id,
                body={"addLabelIds": [label_id], "removeLabelIds": ["UNREAD"]},
            ).execute()
            log.info(f"[{env}] ✓ Correo marcado con la etiqueta '{destino['label']}'.")

            # Los correos más viejos traen listas desactualizadas: si quedaran sin la etiqueta de este
            # entorno, las próximas corridas las tomarían de a una y pisarían los precios vigentes.
            # Con una query manual (workflow_dispatch) se respeta exactamente el correo pedido.
            if ids_anteriores and not GMAIL_SEARCH_QUERY:
                for i in range(0, len(ids_anteriores), 1000):
                    service.users().messages().batchModify(
                        userId="me",
                        body={"ids": ids_anteriores[i:i + 1000], "addLabelIds": [label_id]},
                    ).execute()
                log.info(f"[{env}] ✓ {len(ids_anteriores)} correo(s) anteriores marcados como procesados (descartados).")
        except Exception as e:
            log.warning(f"[{env}] No se pudo aplicar la etiqueta de procesado en Gmail: {e}")

    return todos_ok


# ==============================================================================
# MOTOR 2: IMAP SSL (con App Password de Gmail)
# ==============================================================================
def _leer_adjunto_imap(mail, target_id):
    """(nombre, bytes) del primer adjunto .xlsx/.xls del correo IMAP, o (None, None)."""
    res, msg_data = mail.fetch(target_id, "(RFC822)")
    mail_message = None
    for response_part in msg_data:
        if isinstance(response_part, tuple):
            mail_message = email.message_from_bytes(response_part[1])
            break
    if not mail_message:
        return None, None

    subject = decode_mime_words(mail_message.get("Subject", ""))
    sender = decode_mime_words(mail_message.get("From", ""))
    log.info(f"Correo seleccionado: '{subject}' de {sender}")

    for part in mail_message.walk():
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition") is None:
            continue
        filename = part.get_filename()
        if filename:
            filename = decode_mime_words(filename)
            if filename.lower().endswith((".xlsx", ".xls")):
                return filename, part.get_payload(decode=True)
    return None, None


def ejecutar_con_imap() -> bool:
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        log.error("Modo IMAP: Faltan GMAIL_USER o GMAIL_APP_PASSWORD.")
        return False

    import imaplib

    log.info(f"Conectando por IMAP SSL a Gmail ({GMAIL_USER})...")
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    mail.select("INBOX")

    destinos = entornos_destino()
    todos_ok = bool(destinos)
    adjuntos = {}

    for destino in destinos:
        env = destino["nombre"]
        gm_query = query_para(destino)
        query = gm_query if GMAIL_SEARCH_QUERY else f'X-GM-RAW "{gm_query.replace(chr(34), chr(92) + chr(34))}"'
        log.info(f"[{env}] Buscando por IMAP con query: {query}")
        status, messages = mail.search(None, query)
        mail_ids = messages[0].split()

        if not mail_ids:
            log.info(f"[{env}] No hay correos pendientes para este entorno (etiqueta '{destino['label']}').")
            continue

        # IMAP devuelve los ids en orden ascendente: el último es el más reciente.
        target_id = mail_ids[-1]
        if target_id not in adjuntos:
            adjuntos[target_id] = _leer_adjunto_imap(mail, target_id)
        nombre, datos = adjuntos[target_id]
        if not nombre or not datos:
            log.warning(f"[{env}] No se encontró adjunto Excel (.xlsx/.xls) en el mensaje.")
            todos_ok = False
            continue

        if not enviar_a_backend(destino["url"], destino["key"], env, nombre, datos):
            todos_ok = False
            continue

        try:
            mail.store(target_id, "+X-GM-LABELS", f"({destino['label']})")
            mail.store(target_id, "+FLAGS", "\\Seen")
            log.info(f"[{env}] ✓ Correo marcado con la etiqueta '{destino['label']}'.")
            ids_anteriores = [i for i in mail_ids if i != target_id]
            if ids_anteriores and not GMAIL_SEARCH_QUERY:
                for mid in ids_anteriores:
                    mail.store(mid, "+X-GM-LABELS", f"({destino['label']})")
                log.info(f"[{env}] ✓ {len(ids_anteriores)} correo(s) anteriores marcados como procesados (descartados).")
        except Exception as e:
            log.warning(f"[{env}] No se pudo aplicar la etiqueta por IMAP: {e}")

    mail.close()
    mail.logout()
    return todos_ok


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
