#!/usr/bin/env bash
# (Re-)registers this agent in the khal Agent Catalog. The catalog is the
# source of truth for the deployed app VERSION — the manifest's `version` is
# read from src/agent_app/_version.py automatically, so run this after every
# bump/deploy (scripts/bump_version.sh).
#
# This is the "CD acting as the agent" step of the platform flow: in CI the
# pipeline holds the agent's M2M credential and updates the agent's own
# manifest with it. Auth is identity-only (valid token + right tenant) —
# there are NO scopes in the M2M model.
#
# Token precedence:
#   1. TOKEN                     — explicit token, used as-is
#   2. M2M_CLIENT_ID/SECRET      — with AUTH_SYSTEM_URL: a session is requested
#                                  from the M2M Auth System (client_credentials;
#                                  sessions expire — each run requests a fresh
#                                  one, there is no renew)
#   3. dev claims token          — minted below (base64url JSON, no scopes)
#
# The local catalog stores manifests IN MEMORY — run this again after every
# dev-server restart. Idempotent: an existing agent is updated in place
# (ETag/If-Match handled automatically).
#
# Env overrides (all optional):
#   CATALOG_URL      default http://127.0.0.1:7104 (the Agent Catalog;
#                    legacy spelling REGISTER_URL still honored)
#   TENANT           default acme
#   AGENT_ID         default martino
#   AUTH_SYSTEM_URL  the M2M Auth System base URL (enables the session path)
#   M2M_CLIENT_ID    the agent's M2M credential id
#   M2M_CLIENT_SECRET  the agent's M2M credential secret
#   TOKEN            explicit token for the PUT (wins over everything)
set -euo pipefail

CATALOG_URL="${CATALOG_URL:-${REGISTER_URL:-http://127.0.0.1:7104}}"
TENANT="${TENANT:-acme}"
AGENT_ID="${AGENT_ID:-martino}"

[[ "$AGENT_ID" =~ ^[a-z0-9][a-z0-9-]*$ ]] \
  || { echo "ERROR: AGENT_ID '$AGENT_ID' looks like an empty/invalid expansion"; exit 1; }

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=$(python3 -c "import re,sys;print(re.search(r'__version__ = \"([^\"]+)\"', open(sys.argv[1]).read()).group(1))" "$ROOT/src/agent_app/_version.py")

# Session from the M2M Auth System (the target platform flow): credentials in,
# short-lived session out. No scopes are requested — identity only.
m2m_session() {
  curl -sS -X POST "${AUTH_SYSTEM_URL%/}/oauth/token" \
    -H 'content-type: application/x-www-form-urlencoded' \
    --data-urlencode 'grant_type=client_credentials' \
    --data-urlencode "client_id=${M2M_CLIENT_ID}" \
    --data-urlencode "client_secret=${M2M_CLIENT_SECRET}" \
    | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])"
}

# Dev claims token (base64url JSON read verbatim by the local catalog).
# No scopes — the M2M model is identity-only (valid token + right tenant).
dev_token() {
  python3 -c "
import base64, json, sys
claims = {'tenant': sys.argv[1], 'client_id': 'khal-register-agent.sh'}
print(base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('='))" "$TENANT"
}

if [[ -z "${TOKEN:-}" ]]; then
  if [[ -n "${AUTH_SYSTEM_URL:-}" && -n "${M2M_CLIENT_ID:-}" && -n "${M2M_CLIENT_SECRET:-}" ]]; then
    TOKEN="$(m2m_session)"
    echo "session obtained from the M2M Auth System (${AUTH_SYSTEM_URL})"
  else
    TOKEN="$(dev_token)"
  fi
fi

MANIFEST=$(cat <<EOF
{
  "id": "${AGENT_ID}",
  "manifestVersion": "1.0.0",
  "version": "${VERSION}",
  "info": {
    "name": "${AGENT_ID}",
    "description": "Namastex agent (martino-agent template)"
  },
  "spec": {
    "connectorNeeds": [
      { "signal": "monitoring.trace", "operation": "write" }
    ]
  }
}
EOF
)

# One registration attempt with the given token; prints the body, returns the
# HTTP status via the global CODE. Existing agent → ETag satisfies If-Match.
BODY_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE"' EXIT
attempt() {
  local token="$1"
  local etag
  etag=$(curl -s -o /dev/null -w '%{header_json}' \
    -H "Authorization: Bearer ${token}" \
    "${CATALOG_URL}/agents/${AGENT_ID}" \
    | python3 -c "import json,sys;h=json.load(sys.stdin);print((h.get('etag') or [''])[0])")
  local args=(-sS -X PUT "${CATALOG_URL}/agents/${AGENT_ID}"
    -H "Authorization: Bearer ${token}" -H 'content-type: application/json'
    -o "$BODY_FILE" -w '%{http_code}' -d "${MANIFEST}")
  [[ -n "$etag" ]] && args+=(-H "If-Match: ${etag}")
  CODE=$(curl "${args[@]}")
}

attempt "$TOKEN"

cat "$BODY_FILE"; echo
echo "HTTP ${CODE}"
[[ "$CODE" =~ ^2 ]] || { echo "ERROR: registration failed"; exit 1; }
echo "agent '${AGENT_ID}' v${VERSION} registered at ${CATALOG_URL} (tenant ${TENANT})"
