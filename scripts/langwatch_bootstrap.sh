#!/usr/bin/env bash
# ⚠️ LOCAL DEV ONLY. Bootstraps a from-scratch self-hosted LangWatch on your
# machine: creates the account → org → project, grabs the project API key, and
# writes it to .env. Idempotent (re-runs reuse the existing project's key).
#
#   make langwatch-up && make langwatch-init
#
# In staging/prod you do NOT run this: you point at a managed LangWatch (Cloud or
# a shared self-hosted instance that's already provisioned) by setting
# LANGWATCH_ENDPOINT + LANGWATCH_API_KEY from your secrets manager. This script
# talks better-auth + tRPC and reads the DB directly — it's a dev convenience,
# never part of a deploy.
set -euo pipefail

# Guard: refuse to run against a non-dev environment.
case "${ENVIRONMENT:-${RUNTIME_ENV:-dev}}" in
  prod|production|staging|hml|homolog)
    echo "Refusing: langwatch bootstrap is dev-only (ENVIRONMENT=${ENVIRONMENT:-${RUNTIME_ENV}})." >&2
    echo "In ${ENVIRONMENT:-${RUNTIME_ENV}}, set LANGWATCH_ENDPOINT + LANGWATCH_API_KEY from secrets." >&2
    exit 1 ;;
esac

LANGWATCH_URL="${LANGWATCH_URL:-http://localhost:5560}"
LW_EMAIL="${LW_EMAIL:-dev@namastex.ai}"
LW_PASSWORD="${LW_PASSWORD:-Template-Dev-2026!}"
LW_ORG="${LW_ORG:-Namastex}"
LW_PROJECT="${LW_PROJECT:-agent-template}"
COMPOSE="${COMPOSE:-docker-compose.langwatch.yml}"
ENV_FILE="${ENV_FILE:-.env}"

COOKIES="$(mktemp)"
trap 'rm -f "$COOKIES"' EXIT
say() { printf '\033[36m▸ %s\033[0m\n' "$*"; }

# Tables live in the `mydb` schema; qualify them (no `SET search_path`, whose
# "SET" status line would pollute the single-value output).
pg() { docker compose -f "$COMPOSE" exec -T postgres \
        psql -U prisma -d mydb -t -A -c "$1" 2>/dev/null | tr -d '[:space:]'; }

# 1. Wait for the app to be ready (root serves 200 once migrations are done).
say "Waiting for LangWatch at $LANGWATCH_URL ..."
for _ in $(seq 1 90); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 5 "$LANGWATCH_URL/" || true)
  [ "$code" = "200" ] && break
  sleep 2
done

# 2. Already provisioned? Reuse the existing project's key.
KEY="$(pg "SELECT \"apiKey\" FROM mydb.\"Project\" WHERE name='${LW_PROJECT}' LIMIT 1;" || true)"
if [ -n "$KEY" ]; then
  say "Project '${LW_PROJECT}' already exists — reusing its key."
else
  # 3. Sign up (fresh) or sign in (re-run with a new project on an existing account).
  say "Creating / signing in account ${LW_EMAIL} ..."
  curl -s -m 15 -c "$COOKIES" -H "Origin: $LANGWATCH_URL" -H "Content-Type: application/json" \
    -X POST "$LANGWATCH_URL/api/auth/sign-up/email" \
    -d "{\"email\":\"$LW_EMAIL\",\"password\":\"$LW_PASSWORD\",\"name\":\"Namastex Dev\"}" >/dev/null || true
  if ! grep -q 'session' "$COOKIES" 2>/dev/null; then
    curl -s -m 15 -c "$COOKIES" -H "Origin: $LANGWATCH_URL" -H "Content-Type: application/json" \
      -X POST "$LANGWATCH_URL/api/auth/sign-in/email" \
      -d "{\"email\":\"$LW_EMAIL\",\"password\":\"$LW_PASSWORD\"}" >/dev/null || true
  fi

  # 4. Get-or-create org + team.
  ORG_JSON="$(pg "SELECT id FROM mydb.\"Organization\" WHERE name='${LW_ORG}' LIMIT 1;" || true)"
  if [ -z "$ORG_JSON" ]; then
    say "Creating organization '${LW_ORG}' ..."
    curl -s -m 20 -b "$COOKIES" -H "Origin: $LANGWATCH_URL" -H "Content-Type: application/json" \
      -X POST "$LANGWATCH_URL/api/trpc/organization.createAndAssign?batch=1" \
      -d "{\"0\":{\"json\":{\"orgName\":\"$LW_ORG\"}}}" >/dev/null
  fi
  ORG_ID="$(pg "SELECT id FROM mydb.\"Organization\" WHERE name='${LW_ORG}' ORDER BY \"createdAt\" DESC LIMIT 1;" || true)"
  TEAM_ID="$(pg "SELECT id FROM mydb.\"Team\" WHERE \"organizationId\"='${ORG_ID}' ORDER BY \"createdAt\" DESC LIMIT 1;" || true)"
  [ -n "$ORG_ID" ] && [ -n "$TEAM_ID" ] || { echo "Failed to resolve org/team (auth cookie?)." >&2; exit 1; }

  # 5. Create the project.
  say "Creating project '${LW_PROJECT}' ..."
  curl -s -m 20 -b "$COOKIES" -H "Origin: $LANGWATCH_URL" -H "Content-Type: application/json" \
    -X POST "$LANGWATCH_URL/api/trpc/project.create?batch=1" \
    -d "{\"0\":{\"json\":{\"name\":\"$LW_PROJECT\",\"teamId\":\"$TEAM_ID\",\"organizationId\":\"$ORG_ID\",\"language\":\"python\",\"framework\":\"other\"}}}" >/dev/null

  KEY="$(pg "SELECT \"apiKey\" FROM mydb.\"Project\" WHERE name='${LW_PROJECT}' ORDER BY \"createdAt\" DESC LIMIT 1;")"
fi

[ -n "$KEY" ] || { echo "Failed to obtain a project API key." >&2; exit 1; }

# 6. Write into .env (create from example if missing; upsert the three keys).
[ -f "$ENV_FILE" ] || cp .env.example "$ENV_FILE"
# Replace the key in place if present, else append. Explicit if/else so a failed
# sed never falls through to a duplicate append (the `A && B || C` idiom would).
upsert() {
  if grep -qE "^$1=" "$ENV_FILE"; then
    sed -i.bak -E "s#^$1=.*#$1=$2#" "$ENV_FILE"
  else
    printf '\n%s=%s\n' "$1" "$2" >> "$ENV_FILE"
  fi
}
upsert LANGWATCH_ENABLED 1
upsert LANGWATCH_ENDPOINT "$LANGWATCH_URL"
upsert LANGWATCH_API_KEY "$KEY"
rm -f "$ENV_FILE.bak"

say "Done. LANGWATCH_API_KEY written to $ENV_FILE (${KEY:0:12}...). UI: $LANGWATCH_URL"
