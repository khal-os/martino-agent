# Deploy to AWS ECS Fargate (Copilot)

The agent ships to AWS as a **Load Balanced Web Service** on **ECS Fargate**, backed
by **Aurora Serverless v2 Postgres**, fronted by an **ALB**, with secrets in **SSM**.
Infra is driven by [AWS Copilot](https://aws.github.io/copilot-cli/) — the same
`Dockerfile` that runs locally is built and pushed to ECR; nothing about the app
changes between `make dev` and production.

```
copilot/
  environments/production/manifest.yml   # VPC + ALB + ECS cluster (+ HTTPS hook)
  agent/manifest.yml                      # the Fargate service: :8888, health, secrets
  agent/addons/agentdb.yml                # Aurora Postgres (written by first `aws-up`)
scripts/aws/
  up.sh        redeploy.sh   down.sh
  env-sync.sh  entrypoint.sh
.env.production.example                   # the secret set → SSM via aws-secrets
```

## Prerequisites (once)

- **awscli v2**, authenticated: `aws sso login` (or exported credentials). Verify
  with `aws sts get-caller-identity`.
- **copilot CLI**: `brew install aws/tap/copilot-cli`.
- **Docker** running (Copilot builds the image locally).

## First deploy

```bash
cp .env.production.example .env.production   # fill in API_KEY + ANTHROPIC_API_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"   # → API_KEY

# Point at your account/region (defaults: profile=default, region=us-east-1)
export AWS_PROFILE=namastex AWS_REGION=us-east-1

make aws-up        # provisions everything, syncs secrets, deploys (~15-25 min)
```

`aws-up` is idempotent. On the first run it writes `copilot/agent/addons/agentdb.yml`
(the Aurora addon) — **commit that file** so the database is reproducible.

When it finishes it prints the service URL. Check it:

```bash
curl https://<service-url>/health
curl -X POST https://<service-url>/agents/assistant/runs \
  -H "Authorization: Bearer $API_KEY" \
  -F "message=hello" -F "session_id=demo" -F "stream=false"
```

## Everyday operations

| Task | Command |
|---|---|
| Push new code | `make aws-redeploy` (rebuild image + roll, minutes) |
| Rotate a key | edit `.env.production` → `make aws-secrets` → `make aws-redeploy` |
| Tail logs | `make aws-logs` |
| Shell into the task | `copilot svc exec --app nsmtx-agent --name agent` |
| Tear it all down | `make aws-down` (⚠ destroys the DB) |

## HTTPS (do this before real traffic)

By default the ALB serves **HTTP** on its generated DNS name — so the `API_KEY`
bearer travels in cleartext. Terminate TLS one of two ways:

1. **Domain-managed** — re-init the app with a Route53 domain:
   `copilot app init nsmtx-agent --domain agents.namastex.ai`. Copilot mints ACM
   certs per environment and gives you `agent.production.agents.namastex.ai`.
2. **Import a cert** — uncomment the `http.public.certificates` block in
   `copilot/environments/production/manifest.yml` with an ACM cert ARN in this
   region, then `copilot env deploy`.

## How config & secrets flow

- **Non-secret config** (provider, model tier, workers, `ENVIRONMENT=prod`) →
  `variables:` in `copilot/agent/manifest.yml`.
- **Secrets** (`API_KEY`, model keys) → `.env.production` → `make aws-secrets`
  writes them as SSM SecureString params, referenced by the manifest's `secrets:`.
  They never touch the image or the task definition.
- **`DATABASE_URL`** → Copilot injects the Aurora credentials as a JSON secret;
  `scripts/aws/entrypoint.sh` assembles the URL from it at container start. That
  shim is a no-op locally, so the image is identical dev↔prod.
- **Auth is fail-closed**: `ENVIRONMENT=prod` + a required `API_KEY` secret means
  a misconfigured deploy denies every protected route rather than serving open.

## Knowledge base (pgvector)

`KNOWLEDGE_ENABLED=0` by default. To turn on RAG, connect once and enable the
extension on the Aurora DB, then flip `KNOWLEDGE_ENABLED: 1` in the manifest and
`make aws-redeploy`:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

## Cost (rough, us-east-1)

| Piece | ~Monthly |
|---|---|
| Fargate (1× 0.5 vCPU / 1 GB, always on) | ~$18 |
| ALB | ~$18–25 |
| Aurora Serverless v2 (floor 0.5 ACU) | ~$45 |
| **Total** | **~$80–90** |

Lower the Aurora floor (min ACU) in `copilot/agent/addons/agentdb.yml` for dev
environments, or scale Fargate `count` up for throughput (state is in Postgres, so
replicas are safe).

## Not covered here

CI does **not** deploy — it only runs the offline gate (`make check`) on PRs. This
is a manual/`make aws-redeploy` flow by design. To automate it later, add a
GitHub Actions job gated on `main` that runs `copilot svc deploy` with an OIDC role.
