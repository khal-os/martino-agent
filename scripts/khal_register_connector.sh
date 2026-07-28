#!/usr/bin/env bash
# DEV-ONLY: (re-)registers the LangWatch OTLP connector in a LOCAL khal
# connector-register, so the agent can resolve `monitoring.trace`/`write`
# against the real register instead of any mock.
#
# The local register stores manifests IN MEMORY — run this again after every
# dev-server restart. Idempotent: an existing connector is updated in place
# (ETag/If-Match handled automatically).
#
# For the resolved credential to be REAL (not dev-secret-*), start the register
# with the vault seed (see khal-platform docs/platform/connector-register/sops.md):
#   VAULT_CREDENTIALS_JSON='{"workos-vault://langwatch-cliente":"<api key>"}' \
#     pnpm --filter @khal/connector-register dev
#
# Env overrides (all optional):
#   REGISTER_URL   default http://127.0.0.1:7103
#   TENANT         default acme
#   CONNECTOR_ID   default langwatch-cliente
#   OTLP_ENDPOINT  default http://localhost:5562/api/otel/v1/traces
#   CREDENTIAL_REF default workos-vault://<CONNECTOR_ID> — MUST match a key of
#                  the register's VAULT_CREDENTIALS_JSON for the resolved
#                  credential to be real
set -euo pipefail

REGISTER_URL="${REGISTER_URL:-http://127.0.0.1:7103}"
TENANT="${TENANT:-acme}"
CONNECTOR_ID="${CONNECTOR_ID:-langwatch-cliente}"
OTLP_ENDPOINT="${OTLP_ENDPOINT:-http://localhost:5562/api/otel/v1/traces}"
CREDENTIAL_REF="${CREDENTIAL_REF:-workos-vault://${CONNECTOR_ID}}"

# Dev claims token (base64url JSON) — the local register reads claims verbatim;
# production tokens are real RS256 JWTs from the Auth System.
TOKEN=$(python3 -c "import base64,json,sys;print(base64.urlsafe_b64encode(json.dumps({'tenant':sys.argv[1],'client_id':'khal-register-connector.sh','scope':'connectors.registry:read connectors.registry:write'}).encode()).decode().rstrip('='))" "$TENANT")

BASE_URL="${OTLP_ENDPOINT%/api/otel/v1/traces}"
MANIFEST=$(cat <<EOF
{
  "id": "${CONNECTOR_ID}",
  "manifestVersion": "1.0.0",
  "type": "otlp-stream",
  "connectsTo": "monitoring",
  "capabilities": [
    {
      "signal": "monitoring.trace",
      "operation": "write",
      "bindings": [
        {
          "transport": "http",
          "protocol": "otlp",
          "encoding": "protobuf",
          "endpoint": "${OTLP_ENDPOINT}",
          "auth": { "placement": "header", "name": "authorization", "scheme": "Bearer" }
        }
      ]
    }
  ],
  "baseUrl": "${BASE_URL}",
  "credentialRef": "${CREDENTIAL_REF}",
  "requiredScopes": ["monitoring.trace:write"],
  "lifecycle": "active"
}
EOF
)

# Existing connector? Grab its ETag so the update satisfies If-Match.
ETAG=$(curl -s -o /dev/null -w '%{header_json}' \
  -H "Authorization: Bearer ${TOKEN}" \
  "${REGISTER_URL}/connectors/${CONNECTOR_ID}" \
  | python3 -c "import json,sys;h=json.load(sys.stdin);print((h.get('etag') or [''])[0])")

ARGS=(-sS -X PUT "${REGISTER_URL}/connectors/${CONNECTOR_ID}"
  -H "Authorization: Bearer ${TOKEN}" -H 'content-type: application/json'
  -w '\nHTTP %{http_code}\n' -d "${MANIFEST}")
[[ -n "$ETAG" ]] && ARGS+=(-H "If-Match: ${ETAG}")

curl "${ARGS[@]}"
echo "connector '${CONNECTOR_ID}' registered at ${REGISTER_URL} (tenant ${TENANT}) → ${OTLP_ENDPOINT}"
