"""Knowledge base (optional).

Two supported shapes:

1. **PgVector (scalable, agno-native)** — embeddings + hybrid similarity search,
   backed by the same Postgres you already run. Enabled with ``KNOWLEDGE_ENABLED=1``.
   Seed it once with ``python scripts/seed_knowledge.py`` (reads ``knowledge_base/*.md``).

2. **Filesystem markdown (house style)** — eugenia/renan skip embeddings and let the
   agent grep/read a folder of markdown via filesystem tools. Simpler, no embedder key,
   but no semantic ranking. See docs/architecture.md for that variant.

This module implements option 1 and returns ``None`` when knowledge is disabled,
so the agent still boots without an embedder key.

Docs — Agno PgVector knowledge: https://docs.agno.com/knowledge/vector-stores/pgvector/overview
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .config import Settings

# ─── Embedder builders ───────────────────────────────────────────────────────
# One builder per embedder provider, resolved via the registry below (same shape
# as models.PROVIDERS). Adding a provider = write a `_build_<provider>` +
# register it here — no edits to build_embedder. Imports stay lazy.
EmbedderBuilder = Callable[[Settings], Any]


def _build_openai_embedder(settings: Settings) -> Any:
    from agno.knowledge.embedder.openai import OpenAIEmbedder

    return OpenAIEmbedder(id=settings.embedder_id)


# provider key → builder. Register new embedder providers here (no switch to edit).
EMBEDDER_BUILDERS: dict[str, EmbedderBuilder] = {
    "openai": _build_openai_embedder,
}


def build_embedder(settings: Settings) -> Any:
    provider = settings.embedder_provider.lower()
    builder = EMBEDDER_BUILDERS.get(provider)
    if builder is None:
        raise ValueError(
            f"Unknown EMBEDDER_PROVIDER: {settings.embedder_provider!r} "
            f"(have: {sorted(EMBEDDER_BUILDERS)}). Register one in EMBEDDER_BUILDERS."
        )
    return builder(settings)


def build_knowledge(settings: Settings) -> Any | None:
    """Return an Agno Knowledge base backed by PgVector, or None when disabled."""
    if not settings.knowledge_enabled:
        return None
    if not settings.database_url:
        raise RuntimeError("KNOWLEDGE_ENABLED=1 requires DATABASE_URL (PgVector needs Postgres).")

    from agno.knowledge.knowledge import Knowledge
    from agno.vectordb.pgvector import PgVector, SearchType

    # agno's PgVector wants a SQLAlchemy-style URL.
    db_url = settings.database_url
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

    return Knowledge(
        vector_db=PgVector(
            table_name=settings.knowledge_table,
            db_url=db_url,
            search_type=SearchType.hybrid,
            embedder=build_embedder(settings),
        ),
    )
