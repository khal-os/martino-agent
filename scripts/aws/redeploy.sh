#!/usr/bin/env bash
# Rebuild the image and roll the ECS service — the everyday code-push path (minutes).
# Infra (env, ALB, Aurora) is left untouched; use `make aws-up` for infra changes.
#
#   make aws-redeploy      # or: bash scripts/aws/redeploy.sh
set -euo pipefail

APP="${COPILOT_APP:-nsmtx-agent}"
ENV_NAME="${COPILOT_ENV:-production}"
SVC="${COPILOT_SVC:-agent}"
export AWS_PROFILE="${AWS_PROFILE:-default}" AWS_REGION="${AWS_REGION:-us-east-1}"

cd "$(git rev-parse --show-toplevel)"

# Stamp the running image with the deploy's git sha (surfaces in /health + traces).
GIT_SHA="$(git rev-parse --short HEAD)"
export GIT_SHA
echo "▸ Redeploying $SVC to $ENV_NAME @ $GIT_SHA"

copilot svc deploy --app "$APP" --name "$SVC" --env "$ENV_NAME"
echo "✓ Rolled. Tail logs: copilot svc logs --app $APP --name $SVC --env $ENV_NAME --follow"
