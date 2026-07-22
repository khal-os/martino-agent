#!/bin/sh
# AWS/Copilot container entrypoint.
#
# Copilot's Aurora storage addon injects the DB credentials as an env var holding
# a JSON blob (e.g. AGENTDB_SECRET={"host":...,"username":...,"password":...}).
# The app speaks DATABASE_URL, so assemble one from whichever *_SECRET var carries
# a Postgres credential blob, then hand off to the real command.
#
# This is a NO-OP anywhere DATABASE_URL is already set (local Docker Compose) or
# absent by design (SQLite dev) — so the image behaves identically dev↔prod.
set -e

# The runtime image ships `python`; fall back to `python3` for portability.
PY_BIN="$(command -v python || command -v python3 || true)"

if [ -z "${DATABASE_URL:-}" ] && [ -n "$PY_BIN" ]; then
  _url="$("$PY_BIN" - <<'PY'
import json, os
for key, val in os.environ.items():
    if not key.endswith("_SECRET"):
        continue
    try:
        d = json.loads(val)
    except Exception:
        continue
    if {"host", "username", "password"} <= d.keys():
        host = d["host"]
        port = d.get("port", 5432)
        user = d["username"]
        pw = d["password"]
        db = d.get("dbname") or "agent"
        print(f"postgresql://{user}:{pw}@{host}:{port}/{db}")
        break
PY
)"
  if [ -n "$_url" ]; then
    export DATABASE_URL="$_url"
    echo "entrypoint: assembled DATABASE_URL from injected DB secret" >&2
  fi
fi

exec "$@"
