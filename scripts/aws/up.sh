#!/usr/bin/env bash
# Provision + deploy the agent to AWS ECS Fargate via AWS Copilot.
#
#   make aws-up            # or: bash scripts/aws/up.sh
#
# First run creates everything (VPC, ALB, ECS cluster, Aurora Postgres, ECR) and
# takes ~15-25 min. Re-runs are idempotent and fast — prefer `make aws-redeploy`
# for a plain code push. Requires: awscli v2 logged in (`aws sso login` / creds),
# the `copilot` CLI, and Docker running. Config via env (see defaults below).
set -euo pipefail

APP="${COPILOT_APP:-nsmtx-agent}"
ENV_NAME="${COPILOT_ENV:-production}"
SVC="${COPILOT_SVC:-agent}"
DB_STORAGE="${COPILOT_DB:-agentdb}"
AWS_PROFILE="${AWS_PROFILE:-default}"
REGION="${AWS_REGION:-us-east-1}"
export AWS_PROFILE AWS_REGION="$REGION"

command -v copilot >/dev/null || { echo "✗ copilot CLI not found — https://aws.github.io/copilot-cli/"; exit 1; }
command -v aws >/dev/null || { echo "✗ awscli not found"; exit 1; }
aws sts get-caller-identity >/dev/null || { echo "✗ AWS creds invalid — run 'aws sso login' or set credentials"; exit 1; }

cd "$(git rev-parse --show-toplevel)"

echo "▸ App:$APP  Env:$ENV_NAME  Svc:$SVC  Region:$REGION  Profile:$AWS_PROFILE"

# 1. App (idempotent — the copilot/ manifests are already committed).
copilot app init "$APP" 2>/dev/null || true

# 2. Environment (VPC + ALB + ECS cluster). Skip init if it already exists.
if ! copilot env ls --app "$APP" 2>/dev/null | grep -qx "$ENV_NAME"; then
  copilot env init --app "$APP" --name "$ENV_NAME" --profile "$AWS_PROFILE" --default-config
fi
copilot env deploy --app "$APP" --name "$ENV_NAME"

# 3. Aurora Serverless v2 Postgres, attached to the service. `storage init` writes
#    copilot/${SVC}/addons/${DB_STORAGE}.yml — committed on first run, reused after.
if [ ! -f "copilot/${SVC}/addons/${DB_STORAGE}.yml" ]; then
  copilot storage init \
    --app "$APP" --name "$DB_STORAGE" --storage-type Aurora \
    --workload "$SVC" --engine PostgreSQL --initial-db agent
  echo "▸ Wrote copilot/${SVC}/addons/${DB_STORAGE}.yml — commit it so the DB is reproducible."
fi

# 4. Secrets (API_KEY, model keys) → SSM SecureString, referenced by the manifest.
bash scripts/aws/env-sync.sh

# 5. Build image → push to ECR → roll the service.
copilot svc deploy --app "$APP" --name "$SVC" --env "$ENV_NAME"

echo "✓ Deployed. Service URL:"
copilot svc show --app "$APP" --name "$SVC" --json 2>/dev/null \
  | python -c "import json,sys;d=json.load(sys.stdin);print(' ',[r.get('url') for r in d.get('routes',[])])" 2>/dev/null \
  || copilot svc show --app "$APP" --name "$SVC"
echo "  Health: curl <url>/health"
