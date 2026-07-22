#!/usr/bin/env bash
# Tear down ALL AWS infrastructure for this app — service, ALB, ECS cluster, VPC,
# and the Aurora database (DATA IS DESTROYED). Snapshot the DB first if you need it.
#
#   make aws-down          # or: bash scripts/aws/down.sh
set -euo pipefail

APP="${COPILOT_APP:-nsmtx-agent}"
export AWS_PROFILE="${AWS_PROFILE:-default}" AWS_REGION="${AWS_REGION:-us-east-1}"

cd "$(git rev-parse --show-toplevel)"

read -r -p "⚠  Delete app '$APP' and its Aurora DB in $AWS_REGION? Type the app name to confirm: " reply
[ "$reply" = "$APP" ] || { echo "Aborted."; exit 1; }

copilot app delete --name "$APP" --yes
echo "✓ Torn down."
