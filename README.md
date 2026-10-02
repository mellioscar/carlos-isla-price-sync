# Carlos Isla — Sincronización Automática de Precios desde Gmail

Pipeline automatizado en **GitHub Actions** para el **Portal de Clientes de Carlos Isla y Cía.**
Descarga automáticamente la lista de precios más reciente en formato Excel enviada a una cuenta de Gmail y la sincroniza en el Backend (QA, Producción o Ambos).

---

## 1. Flujo Operativo

1. **Búsqueda en Gmail**: 
   - Para **cada entorno** busca el correo más reciente con asunto `Portal Clientes - Precios` (configurable) y adjunto que **no** tenga la etiqueta de ese entorno: `PORTAL_PRECIOS_QA` o `PORTAL_PRECIOS_PROD`. Así una corrida solo-QA no deja a Producción sin la lista. (La etiqueta histórica `PORTAL_PRECIOS_ACTUALIZADOS` cuenta como procesado solo para QA.)
2. **Descarga de Adjunto**: 
   - Extrae el archivo `.xlsx` o `.xls` adjunto (ejemplo: `Lista de Precios Con Stock UM Pred. Vta..xlsx`).
3. **Sincronización en Backend**: 
   - Realiza una petición HTTP `POST /products/sync` (multipart/form-data) autenticada mediante el encabezado `x-api-key`.
   - El backend actualiza **solo los precios** de los artículos que **ya existen** en el portal (el stock se toma de NET-LogistK, no del Excel), comparando el código como texto exacto (respeta ceros a la izquierda, barras y guiones). No crea ni quita artículos y no modifica nombre ni estado.
   - Las diferencias (códigos del Excel que no existen en el portal y artículos activos que no vinieron en el Excel) quedan en un informe que el administrador revisa en **Gestión de productos ➔ Diferencias**, donde decide qué agregar, desactivar o eliminar. El log del workflow también las informa.
   - Si ningún código del Excel coincide con el portal (archivo o columna equivocados), la sincronización falla y el correo no se etiqueta.
4. **Etiquetado y Auditoría**: 
   - Si el backend responde con éxito (`HTTP 200` con al menos un artículo actualizado):
     - Crea la etiqueta del entorno (`PORTAL_PRECIOS_QA` / `PORTAL_PRECIOS_PROD`) en Gmail si no existe y la aplica solo si ese entorno se sincronizó bien.
     - Marca el correo con esa etiqueta y lo marca como leído (evitando reprocesar el mismo correo en las siguientes ejecuciones).
     - Marca también como procesados los correos **más viejos** con el mismo asunto que hubieran quedado pendientes: traen listas desactualizadas y, si no, se procesarían en las corridas siguientes pisando los precios vigentes (no aplica a búsquedas manuales con `gmail_query`).
     - El backend guarda en la tabla `AppConfig` la fecha exacta, estado `SUCCESS`, origen `GITHUB_ACTIONS_GMAIL` y total de filas sincronizadas, que se reflejan en tiempo real en la pantalla de Configuración del Administrador (`/dashboard/admin-config`).

---

## 2. Soporte Multi-Entorno (QA, PROD y AMBOS)

El pipeline puede sincronizar indistintamente en **QA**, **Producción** o en **Ambos al mismo tiempo** con el mismo archivo:

- **Ejecución Manual (`workflow_dispatch`)**:
  - En la pestaña **Actions** de GitHub, al presionar **Run workflow**, un menú desplegable permite seleccionar el entorno de destino:
    - `QA`: Sincroniza únicamente en el backend de QA.
    - `PROD`: Sincroniza únicamente en el backend de Producción.
    - `AMBOS`: Sincroniza primero en QA y luego en Producción.
  - Parámetro opcional `gmail_query`: Permite ingresar una búsqueda personalizada para forzar un correo específico (ej: `subject:"Lista de Precios" after:2026/09/01`).

- **Cron Programado (`schedule`)**:
  - Corre de **Lunes a Sábados cada 2 horas** entre las 07:50 y las 17:50 hora Argentina:
    ```yaml
    - cron: '50 10-20/2 * * 1-6' # 10:50, 12:50, 14:50, 16:50, 18:50, 20:50 UTC (UTC-3 = ART)
    ```
  - En las ejecuciones automáticas sincroniza para `AMBOS` entornos.
  - **El `schedule` de GitHub no es puntual** (se atrasa horas y a veces se saltea corridas). Para horarios exactos, el disparo lo hace **AWS EventBridge Scheduler** (zona `America/Argentina/Buenos_Aires`, 07:50 a 17:50 cada 2 h, lun–sáb) llamando a:
    ```
    POST https://api.github.com/repos/mellioscar/carlos-isla-price-sync/actions/workflows/sync-prices-gmail.yml/dispatches
    Authorization: Bearer <fine-grained token, solo 'Actions: write' en este repo>
    {"ref": "main", "inputs": {"target_env": "AMBOS"}}
    ```
    El cron de GitHub queda como respaldo.

---

## 3. Configuración de Secrets en GitHub

En el repositorio de GitHub, ve a **Settings** ➔ **Secrets and variables** ➔ **Actions** y configura:

### A. Endpoints y Claves del Backend

| Secret | Descripción | Ejemplo / Valor |
| :--- | :--- | :--- |
| `BACKEND_SYNC_URL_QA` | URL de sincronización de QA | `https://qa-api.clientes.carlosisla.com.ar/products/sync` |
| `EXTERNAL_SYNC_API_KEY_QA` | API Key para QA (igual a `EXTERNAL_SYNC_API_KEY` del secreto de QA en AWS) | *(secreta, nunca en el repo)* |
| `BACKEND_SYNC_URL_PROD` | URL de sincronización de Producción | `https://api-clientes.carlosisla.com.ar/products/sync` |
| `EXTERNAL_SYNC_API_KEY_PROD` | API Key para Producción (igual a `EXTERNAL_SYNC_API_KEY` del secreto de PROD en AWS) | *(secreta, nunca en el repo)* |

### B. Credenciales de Gmail (Google API OAuth — Modo NET-LogistK)

| Secret | Descripción | Contenido |
| :--- | :--- | :--- |
| `GMAIL_CREDENTIALS_JSON` | Archivo cliente OAuth de Google Cloud | Todo el contenido de `credentials.json` |
| `GMAIL_TOKEN_JSON` | Token de acceso y refresh token | Todo el contenido de `token.json` |

*(Alternativa IMAP: si se prefiere usar IMAP con contraseña de aplicación en lugar de OAuth, se pueden definir `GMAIL_USER` y `GMAIL_APP_PASSWORD`).*

### C. Variables Opcionales de Filtrado

| Secret | Descripción | Valor por Defecto |
| :--- | :--- | :--- |
| `GMAIL_SUBJECT_FILTER` | Asunto del correo a buscar | `Portal Clientes - Precios` |
| `GMAIL_LABEL_QA` | Etiqueta de procesado para QA | `PORTAL_PRECIOS_QA` |
| `GMAIL_LABEL_PROD` | Etiqueta de procesado para Producción | `PORTAL_PRECIOS_PROD` |
| `GMAIL_LABEL_PROCESADO` | Etiqueta histórica (anterior a las etiquetas por entorno) | `PORTAL_PRECIOS_ACTUALIZADOS` |

---

## 4. ¿Cómo actualizar el Token de Google OAuth si se renueva?

Si alguna vez necesitas renovar la sesión de Google o actualizar el token:

1. El archivo `token.json` contiene la estructura:
   ```json
   {
     "token": "ya29...",
     "refresh_token": "1//...",
     "token_uri": "https://oauth2.googleapis.com/token",
     "client_id": "...",
     "client_secret": "...",
     "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
     "universe_domain": "googleapis.com"
   }
   ```
2. Lo importante es que el campo `refresh_token` sea válido.
3. Copia todo el contenido JSON del archivo `token.json` renovado.
4. Ve a GitHub ➔ **Settings** ➔ **Secrets and variables** ➔ **Actions** ➔ edita el secret **`GMAIL_TOKEN_JSON`** y reemplaza su valor.

---

## 5. Estructura del Repositorio

```
carlos-isla-price-sync/
├── .github/
│   └── workflows/
│       └── sync-prices-gmail.yml    # Workflow de GitHub Actions (Cron + Workflow Dispatch)
├── .gitignore                       # Ignora credenciales locales (credentials.json, token.json, .env)
├── requirements.txt                 # requests, google-api-python-client, python-dotenv
├── sync_prices_from_gmail.py        # Script de sincronización con soporte multi-entorno y etiquetado
└── README.md                        # Esta documentación
```

---

## 6. Monitoreo en el Portal Web

Una vez completada la sincronización, el Administrador puede ingresar al Portal de Clientes en:  
👉 **Administrador** ➔ **Configuración** (`/dashboard/admin-config`)

Allí visualizará la tarjeta:
- **Última sincronización**: Fecha y hora exacta.
- **Origen**: `GITHUB_ACTIONS_GMAIL` (o `MANUAL` si se subió por interfaz).
- **Artículos impactados**: Cantidad de filas procesadas.
- **Estado**: Badge verde `SUCCESS` o advertencia `WARNING`.
