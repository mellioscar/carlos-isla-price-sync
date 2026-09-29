# Carlos Isla — Sincronización Automática de Precios desde Gmail

Pipeline automatizado para el Portal de Clientes de Carlos Isla y Cía.
Descarga automáticamente la lista de precios más reciente enviada a Gmail y la sincroniza en el Backend mediante GitHub Actions.

## Flujo Operativo

1. **Búsqueda en Gmail**: Localiza correos con asunto Portal Clientes - Precios que no posean la etiqueta PORTAL_PRECIOS_ACTUALIZADOS.
2. **Descarga de Adjunto**: Extrae el archivo .xlsx o .xls.
3. **Sincronización en Backend**: Realiza un POST /products/sync con el archivo y la cabecera de autenticación x-api-key.
4. **Etiquetado y Auditoría**: Si el backend responde con éxito, marca el correo con la etiqueta PORTAL_PRECIOS_ACTUALIZADOS y lo marca como leído. El backend actualiza la fecha, filas procesadas y estado en la base de datos para visualización en el panel administrativo.

## Modos de Conexión Soportados

- **Google API v1 (OAuth)**: Utiliza credentials.json y 	oken.json (mismo mecanismo que NET-LogistK).
- **Gmail IMAP SSL**: Utiliza GMAIL_USER y GMAIL_APP_PASSWORD (Contraseña de aplicación de Google).

## Secrets de GitHub Actions

Configurar en Settings -> Secrets and variables -> Actions:

- BACKEND_SYNC_URL: Endpoint de sincronización (ej: https://api.clientes.carlosisla.com.ar/products/sync).
- EXTERNAL_SYNC_API_KEY: Clave configurada en el backend para autorizar la sincronización.
- GMAIL_USER y GMAIL_APP_PASSWORD (para modo IMAP).
- GMAIL_CREDENTIALS_JSON y GMAIL_TOKEN_JSON (para modo OAuth).
- GMAIL_SUBJECT_FILTER: (Opcional, por defecto Portal Clientes - Precios).
- GMAIL_LABEL_PROCESADO: (Opcional, por defecto PORTAL_PRECIOS_ACTUALIZADOS).
