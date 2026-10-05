#!/usr/bin/env bash
# Crea (o actualiza) en AWS un disparador EventBridge que ejecuta el workflow de GitHub a horario exacto.
#
# Por qué: el `schedule` de GitHub Actions es "best effort" y en este repo ejecutó cerca de un tercio de
# las corridas esperadas (se atrasa horas o directamente no dispara). EventBridge llama a la API
# `workflow_dispatch` de GitHub, que sí corre siempre. El cron de GitHub queda solo como respaldo.
#
# Uso (en AWS CloudShell, región us-east-1):
#   1) Crear un token en GitHub: Settings > Developer settings > Personal access tokens > Fine-grained tokens
#      - Repository access: "Only select repositories" > mellioscar/carlos-isla-price-sync
#      - Permissions > Repository permissions > Actions: "Read and write"
#      - Expiration: lo máximo permitido (1 año). Anotar la fecha de vencimiento para renovarlo.
#   2) export GITHUB_TOKEN='github_pat_xxxxx'     (no queda guardado en ningún archivo ni historial del repo)
#   3) bash aws/crear-disparador-eventbridge.sh
#
# Es idempotente: se puede volver a correr para rotar el token o cambiar el horario.
# Horario (EventBridge usa UTC; Argentina = UTC-3 sin horario de verano):
#   10:45, 12:45, 14:45, 16:45, 18:45 y 20:45 UTC  =  07:45 a 17:45 hora de Argentina, de lunes a sábado.
#   (El ERP envía el correo a los :30 de esas horas: cron "30 7-17/2 * * 1-6".)
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
REPO="mellioscar/carlos-isla-price-sync"
WORKFLOW="sync-prices-gmail.yml"
ENTORNO="PROD"   # el disparo automático actualiza solo Producción; QA se actualiza a mano
CRON="cron(45 10,12,14,16,18,20 ? * MON-SAT *)"

NOMBRE_CONEXION="price-sync-github"
NOMBRE_DESTINO="price-sync-github-dispatch"
NOMBRE_ROL="price-sync-eventbridge-invoke"
NOMBRE_REGLA="price-sync-disparo-prod"

: "${GITHUB_TOKEN:?Definí GITHUB_TOKEN (fine-grained, solo $REPO, permiso Actions: Read and write)}"

echo "== 1/5 Conexión con GitHub (guarda el token en Secrets Manager, administrado por EventBridge) =="
AUTH_PARAMS="{\"ApiKeyAuthParameters\":{\"ApiKeyName\":\"Authorization\",\"ApiKeyValue\":\"Bearer ${GITHUB_TOKEN}\"}}"
if aws events describe-connection --name "$NOMBRE_CONEXION" --region "$REGION" >/dev/null 2>&1; then
  aws events update-connection --name "$NOMBRE_CONEXION" --authorization-type API_KEY \
    --auth-parameters "$AUTH_PARAMS" --region "$REGION" >/dev/null
  echo "Conexión actualizada (token rotado)."
else
  aws events create-connection --name "$NOMBRE_CONEXION" --authorization-type API_KEY \
    --auth-parameters "$AUTH_PARAMS" --region "$REGION" >/dev/null
  echo "Conexión creada."
fi
ARN_CONEXION=$(aws events describe-connection --name "$NOMBRE_CONEXION" --region "$REGION" --query ConnectionArn --output text)

echo "== 2/5 Destino de API: POST a la API de GitHub =="
ENDPOINT="https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches"
if aws events describe-api-destination --name "$NOMBRE_DESTINO" --region "$REGION" >/dev/null 2>&1; then
  aws events update-api-destination --name "$NOMBRE_DESTINO" --connection-arn "$ARN_CONEXION" \
    --invocation-endpoint "$ENDPOINT" --http-method POST --invocation-rate-limit-per-second 1 \
    --region "$REGION" >/dev/null
else
  aws events create-api-destination --name "$NOMBRE_DESTINO" --connection-arn "$ARN_CONEXION" \
    --invocation-endpoint "$ENDPOINT" --http-method POST --invocation-rate-limit-per-second 1 \
    --region "$REGION" >/dev/null
fi
ARN_DESTINO=$(aws events describe-api-destination --name "$NOMBRE_DESTINO" --region "$REGION" --query ApiDestinationArn --output text)

echo "== 3/5 Rol de IAM para que la regla pueda invocar el destino =="
if ! aws iam get-role --role-name "$NOMBRE_ROL" >/dev/null 2>&1; then
  aws iam create-role --role-name "$NOMBRE_ROL" --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{"Effect":"Allow","Principal":{"Service":"events.amazonaws.com"},"Action":"sts:AssumeRole"}]
  }' >/dev/null
  echo "Rol creado; esperando propagación..."
  sleep 10
fi
aws iam put-role-policy --role-name "$NOMBRE_ROL" --policy-name invocar-destino-github --policy-document "{
  \"Version\":\"2012-10-17\",
  \"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"events:InvokeApiDestination\",\"Resource\":\"${ARN_DESTINO}\"}]
}"
ARN_ROL=$(aws iam get-role --role-name "$NOMBRE_ROL" --query Role.Arn --output text)

echo "== 4/5 Regla con el horario =="
aws events put-rule --name "$NOMBRE_REGLA" --schedule-expression "$CRON" --state ENABLED \
  --description "Dispara el workflow de precios (${ENTORNO}) en ${REPO}" --region "$REGION" >/dev/null

echo "== 5/5 Objetivo de la regla =="
TARGETS_FILE="$(mktemp)"
cat > "$TARGETS_FILE" <<EOF
[{
  "Id": "github-dispatch",
  "Arn": "${ARN_DESTINO}",
  "RoleArn": "${ARN_ROL}",
  "Input": "{\"ref\":\"main\",\"inputs\":{\"target_env\":\"${ENTORNO}\"}}",
  "HttpParameters": {
    "HeaderParameters": {
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28"
    }
  }
}]
EOF
aws events put-targets --rule "$NOMBRE_REGLA" --targets "file://${TARGETS_FILE}" --region "$REGION"
rm -f "$TARGETS_FILE"

echo
echo "Listo. Regla '${NOMBRE_REGLA}': ${CRON}  (UTC)"
echo "Verificación: en GitHub > Actions, en la próxima hora programada debe aparecer una corrida con el evento"
echo "'workflow_dispatch' (no 'schedule'), a los :45 (+-1 min). Si no aparece, mirar en CloudWatch las métricas"
echo "de la regla (FailedInvocations): 401/403 = token mal copiado o sin permiso 'Actions: Read and write'."
