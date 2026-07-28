"""Typed, fail-fast application settings.

House style (see genie-hv-eugenia / renan): a single frozen ``Settings`` object,
loaded from the environment via ``get_settings()`` (``.env`` is loaded first for
local dev). Required secrets fail *at boot* with a clear message instead of
blowing up on the first request.

Note: env is read at **instantiation time** (``_from_env`` factory), not at class
definition — so tests and multi-config tooling can change ``os.environ`` and call
``get_settings.cache_clear()`` to rebuild.

Design trade-offs (deliberate, for a small template):
- The whole frozen ``Settings`` is passed to the ``build_*`` factories rather than
  hand-picking each field. It trades a slightly wider dependency for far less
  plumbing; a builder simply reads the two or three fields it needs.
- ``get_settings()`` is the process-cached accessor used as the single injection
  point. Call sites that reach for it directly (e.g. ``agents/__init__``) do so for
  ergonomics; they stay testable via ``get_settings.cache_clear()``.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from ._version import __version__

# Load .env from the repo root (and a local override) before reading anything.
_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")
load_dotenv(_ROOT / ".env.local", override=True)


def require_env(name: str) -> str:
    """Read an env var, failing fast with a clear message when it's missing."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # --- Identity ---
    agent_id: str
    agent_name: str

    # --- Model ---
    # provider ∈ {anthropic, openai, google, openrouter, litellm}
    # Model IDs age fast — check the provider's live models API before pinning.
    model_provider: str
    model_id: str
    model_base_url: str | None
    model_api_key: str | None

    # --- Model fallback (optional; empty = disabled) ---
    fallback_provider: str
    fallback_model_id: str

    # --- Persistence ---
    database_url: str | None
    sqlite_file: str

    # --- Knowledge ---
    knowledge_enabled: bool
    knowledge_table: str
    embedder_provider: str
    embedder_id: str

    # --- Serving ---
    host: str
    port: int
    workers: int
    api_key: str | None

    # --- Prompt caching (Anthropic) ---
    prompt_cache: bool
    cache_extended_ttl: bool
    build_date: str

    # --- Observability (connector register) ---
    # The observability settings in the env: where the per-client khal
    # connector-register lives and the token it requires. Either unset →
    # tracing off. Everything else (trace endpoints, credentials, TTL) is
    # resolved from the register at runtime — never configure a vendor
    # address here. See connector.py and docs/observability.md.
    connector_register_url: str | None
    connector_register_token: str | None  # M2M token (dev: base64url claims token)
    # Trace metadata (rich by default):
    service_name: str  # OTel service.name (Resource) — was "unknown_service"
    agent_instance: str  # this deployment/replica (AGENT_INSTANCE, default: hostname)
    environment: str  # deployment.environment: dev | staging | prod
    agent_version: str  # the agent's semantic version (single source: _version.py)
    git_sha: str  # build/deploy provenance (GIT_SHA in CI), "unknown" locally
    channel: str  # default channel type for non-omni entry points (omni overrides per-request)
    domain: str | None  # business domain of this deployment (platform trace filter)
    subdomain: str | None  # business subdomain of this deployment

    # --- Logging ---
    log_level: str  # DEBUG | INFO | WARNING | ERROR
    log_json: bool  # JSON lines (prod) vs pretty console (dev)

    # --- Experiments (A/B) ---
    experiments_store_path: str  # runtime traffic-weight overrides (JSON; see experiments/store.py)

    # --- Omni (omnichannel webhook adapter) ---
    omni_agent_id: str  # agent that answers Omni channel messages (baseline mode)
    omni_experiment: str | None  # if set, route Omni messages through this A/B experiment

    # --- Behaviour ---
    debug: bool

    # --- Paths ---
    root_dir: Path
    knowledge_dir: Path


def _from_env() -> Settings:
    """Build Settings by reading the environment NOW (not at import time)."""
    environment = os.getenv("ENVIRONMENT", os.getenv("RUNTIME_ENV", "dev"))
    return Settings(
        agent_id=os.getenv("AGENT_ID", "assistant"),
        agent_name=os.getenv("AGENT_NAME", "Assistant"),
        model_provider=os.getenv("MODEL_PROVIDER", "anthropic"),
        model_id=os.getenv("MODEL_ID", "claude-sonnet-5"),
        model_base_url=os.getenv("MODEL_BASE_URL") or None,
        model_api_key=os.getenv("MODEL_API_KEY") or None,
        fallback_provider=os.getenv("FALLBACK_PROVIDER", ""),
        fallback_model_id=os.getenv("FALLBACK_MODEL_ID", ""),
        database_url=os.getenv("DATABASE_URL") or None,
        sqlite_file=os.getenv("SQLITE_FILE", "tmp/agent.db"),
        knowledge_enabled=_flag("KNOWLEDGE_ENABLED", default=False),
        knowledge_table=os.getenv("KNOWLEDGE_TABLE", "knowledge"),
        embedder_provider=os.getenv("EMBEDDER_PROVIDER", "openai"),
        embedder_id=os.getenv("EMBEDDER_ID", "text-embedding-3-small"),
        host=os.getenv("HOST", "0.0.0.0"),  # noqa: S104 — bind all interfaces (container default)
        port=int(os.getenv("PORT", "8888")),
        workers=int(os.getenv("API_WORKERS", "2")),
        api_key=os.getenv("API_KEY") or None,
        prompt_cache=_flag("PROMPT_CACHE", default=True),
        cache_extended_ttl=_flag("CACHE_EXTENDED_TTL", default=True),
        # Local calendar date is intentional here (cache-key freshness, not a timestamp).
        build_date=os.getenv("BUILD_DATE", date.today().isoformat()),  # noqa: DTZ011
        connector_register_url=os.getenv("CONNECTOR_REGISTER_URL") or None,
        connector_register_token=os.getenv("CONNECTOR_REGISTER_TOKEN") or None,
        service_name=os.getenv("OTEL_SERVICE_NAME") or os.getenv("AGENT_ID") or "assistant",
        agent_instance=os.getenv("AGENT_INSTANCE") or socket.gethostname(),
        environment=environment,
        agent_version=__version__,
        git_sha=os.getenv("GIT_SHA", "unknown"),
        channel=os.getenv("CHANNEL", "api"),
        domain=os.getenv("DOMAIN") or None,
        subdomain=os.getenv("SUBDOMAIN") or None,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        # Default: structured JSON everywhere except local dev (pretty console there).
        log_json=_flag("LOG_JSON", default=(environment != "dev")),
        experiments_store_path=os.getenv(
            "EXPERIMENTS_STORE_PATH", "tmp/experiment_allocations.json"
        ),
        omni_agent_id=os.getenv("OMNI_AGENT_ID") or os.getenv("AGENT_ID") or "assistant",
        omni_experiment=os.getenv("OMNI_EXPERIMENT") or None,
        debug=_flag("DEBUG", default=False),
        root_dir=_ROOT,
        knowledge_dir=_ROOT / "src" / "agent_app" / "knowledge_base",
    )


@lru_cache
def get_settings() -> Settings:
    return _from_env()
