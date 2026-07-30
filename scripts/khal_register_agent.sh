#!/usr/bin/env bash
# DEV-ONLY: (re-)registers this agent in a LOCAL khal agent-register. The
# register is the source of truth for the deployed app VERSION — the manifest's
# `version` is read from src/agent_app/_version.py automatically, so run this
# after every bump/deploy (scripts/bump_version.sh).
#
# In the target platform flow (SPEC-1), this PUT also provisions the agent's
# M2M credential in the Auth System; the returned client_secret then feeds
# POST /agents/{id}/token, whose token goes into the agent's M2M_TOKEN env.
#
# The local register stores manifests IN MEMORY — run this again after every
# dev-server restart. Idempotent: an existing agent is updated in place
# (ETag/If-Match handled automatically).
#
# Env overrides (all optional):
#   REGISTER_URL  default http://127.0.0.1:7104 (the agent-register)
#   TENANT        default acme
#   AGENT_ID      default martino
#   TOKEN         a USER token for the PUT (registration is a user action).
#                 Unset → dev claims token minted below.
set -euo pipefail

REGISTER_URL="${REGISTER_URL:-http://127.0.0.1:7104}"
TENANT="${TENANT:-acme}"
AGENT_ID="${AGENT_ID:-martino}"

[[ "$AGENT_ID" =~ ^[a-z0-9][a-z0-9-]*$ ]] \
  || { echo "ERROR: AGENT_ID '$AGENT_ID' looks like an empty/invalid expansion"; exit 1; }

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=$(python3 -c "import re,sys;print(re.search(r'__version__ = \"([^\"]+)\"', open(sys.argv[1]).read()).group(1))" "$ROOT/src/agent_app/_version.py")

# LEGACY until SPEC-3 lands: the local register still guards routes by scope
# (agents:read / agents:write), so the dev claims token carries them. Once the
# platform removes scopes, drop the scope field here. A real user token can be
# passed via TOKEN instead.
TOKEN="${TOKEN:-$(python3 -c "import base64,json,sys;print(base64.urlsafe_b64encode(json.dumps({'tenant':sys.argv[1],'client_id':'khal-register-agent.sh','scope':'agents:read agents:write'}).encode()).decode().rstrip('='))" "$TENANT")}"

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

# Existing agent? Grab its ETag so the update satisfies If-Match.
ETAG=$(curl -s -o /dev/null -w '%{header_json}' \
  -H "Authorization: Bearer ${TOKEN}" \
  "${REGISTER_URL}/agents/${AGENT_ID}" \
  | python3 -c "import json,sys;h=json.load(sys.stdin);print((h.get('etag') or [''])[0])")

ARGS=(-sS -X PUT "${REGISTER_URL}/agents/${AGENT_ID}"
  -H "Authorization: Bearer ${TOKEN}" -H 'content-type: application/json'
  -w '\nHTTP %{http_code}\n' -d "${MANIFEST}")
[[ -n "$ETAG" ]] && ARGS+=(-H "If-Match: ${ETAG}")

curl "${ARGS[@]}"
echo "agent '${AGENT_ID}' v${VERSION} registered at ${REGISTER_URL} (tenant ${TENANT})"
