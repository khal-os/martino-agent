# syntax=docker/dockerfile:1
# uv-based build. Non-root runtime, deps resolved from pyproject (no hand-pinned list).
FROM python:3.12-slim AS base

# uv: fast, reproducible installs (matches eugenia/renan house style).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install the app + ALL declared dependencies straight from pyproject — the single
# source of deps (incl. google-genai). Needs README.md (readme field) and
# src/agent_app/_version.py (dynamic hatch version), so copy those first.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv && uv pip install -e .

COPY scripts/ ./scripts/

# Non-root user.
RUN groupadd -r app && useradd -r -g app -u 10001 app \
    && mkdir -p /app/tmp && chown -R app:app /app
USER app

EXPOSE 8888

# Liveness (/livez) — NOT /health: /health is readiness (503 on DB down) and would
# restart-loop a healthy app during a transient DB outage.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8888/livez').status==200 else 1)" || exit 1

# Uvicorn with multiple workers for concurrency (scale horizontally with replicas).
CMD ["uvicorn", "agent_app.main:app", "--host", "0.0.0.0", "--port", "8888", "--workers", "2"]
