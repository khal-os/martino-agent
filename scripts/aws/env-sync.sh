#!/usr/bin/env bash
# Push secrets from .env.production into AWS SSM (SecureString) via `copilot secret
# init`. The service manifest references these by name — they are NEVER baked into
# the image or task definition. Re-run any time a key rotates; it overwrites.
#
#   make aws-secrets       # or: bash scripts/aws/env-sync.sh
#
# Only the keys listed in SECRET_KEYS below are synced, and only if present and
# non-empty in .env.production. Everything else (non-secret config) lives in the
# manifest's `variables:` block.
set -euo pipefail

APP="${COPILOT_APP:-nsmtx-agent}"
ENV_NAME="${COPILOT_ENV:-production}"
ENV_FILE="${ENV_FILE:-.env.production}"
export AWS_PROFILE="${AWS_PROFILE:-default}" AWS_REGION="${AWS_REGION:-us-east-1}"

cd "$(git rev-parse --show-toplevel)"
[ -f "$ENV_FILE" ] || { echo "✗ $ENV_FILE not found — copy .env.production.example and fill it in."; exit 1; }

# The sensitive keys. Add provider/fallback/observability keys as you enable them.
SECRET_KEYS="API_KEY ANTHROPIC_API_KEY OPENAI_API_KEY GOOGLE_API_KEY OPENROUTER_API_KEY FALLBACK_API_KEY LANGWATCH_API_KEY"

synced=0
for key in $SECRET_KEYS; do
  # Read KEY=value from the env file without sourcing it (values may hold spaces/#).
  val="$(grep -E "^${key}=" "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
  val="${val%\"}"; val="${val#\"}"   # strip optional surrounding quotes
  [ -n "$val" ] || continue
  echo "▸ syncing $key"
  copilot secret init --app "$APP" --name "$key" --values "${ENV_NAME}=${val}" --overwrite >/dev/null
  synced=$((synced + 1))
done

echo "✓ synced $synced secret(s) to $APP/$ENV_NAME"
[ "$synced" -gt 0 ] || echo "  (nothing synced — is $ENV_FILE filled in?)"
