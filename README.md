# Carlos Isla — Sincronización Automática de Precios desde Gmail

Pipeline automatizado en **GitHub Actions** para el **Portal de Clientes de Carlos Isla y Cía.**
Descarga automáticamente la lista de precios más reciente en formato Excel enviada a una cuenta de Gmail y la sincroniza en el Backend (QA, Producción o Ambos).

---

## 1. Flujo Operativo

1. **Búsqueda en Gmail**: 
   - Localiza el correo con asunto `Portal Clientes - Precios` (configurable) que **NO** posea la etiqueta `PORTAL_PRECIOS_ACTUALIZADOS` y tenga un archivo adjunto.
2. **Descarga de Adjunto**: 
   - Extrae el archivo `.xlsx` o `.xls` adjunto (ejemplo: `Lista de Precios Con Stock UM Pred. Vta..xlsx`).
3. **Sincronización en Backend**: 
   - Realiza una petición HTTP `POST /products/sync` (multipart/form-data) autenticada mediante el encabezado `x-api-key`.
   - El backend procesa en bloque (*Bulk Upsert*) los precios, coeficientes y stocks de miles de artículos en segundos sin tiempo de inactividad.
4. **Etiquetado y Auditoría**: 
   - Si el backend responde con éxito (`HTTP 200` y `success: true`):
     - Crea la etiqueta `PORTAL_PRECIOS_ACTUALIZADOS` en Gmail si no existe.
     - Marca el correo con esa etiqueta y lo marca como leído (evitando reprocesar el mismo correo en las siguientes ejecuciones).
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

---

## 3. Configuración de Secrets en GitHub

En el repositorio de GitHub, ve a **Settings** ➔ **Secrets and variables** ➔ **Actions** y configura:

### A. Endpoints y Claves del Backend

| Secret | Descripción | Ejemplo / Valor |
| :--- | :--- | :--- |
| `BACKEND_SYNC_URL_QA` | URL de sincronización de QA | `https://qa-api.clientes.carlosisla.com.ar/products/sync` |
| `EXTERNAL_SYNC_API_KEY_QA` | API Key para QA | `carlos-isla-sync-key-2025` |
| `BACKEND_SYNC_URL_PROD` | URL de sincronización de Producción | `https://api-clientes.carlosisla.com.ar/products/sync` |
| `EXTERNAL_SYNC_API_KEY_PROD` | API Key para Producción | `carlos-isla-sync-key-2025` *(o la de producción)* |

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
| `GMAIL_LABEL_PROCESADO` | Etiqueta para marcar correos procesados | `PORTAL_PRECIOS_ACTUALIZADOS` |

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
