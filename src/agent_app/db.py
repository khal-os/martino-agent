"""Database wiring.

Sessions, memory and session-state persist here. In production use Postgres
(``DATABASE_URL``); with nothing configured the app falls back to a local SQLite
file so the template boots with zero infra.

House note: eugenia/renan wrap agno's ``PostgresDb`` in a "resilient" subclass to
survive an upstream reconnect bug. That's omitted here to keep the template small
— add it if you see sessions vanish on DB flaps (agno-agi/agno#8196).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import Settings


@lru_cache
def build_db(settings: Settings) -> Any:
    """Return an Agno db for sessions/memory/state.

    Cached on ``settings`` so every agent (including A/B variants) shares ONE db
    instance. That's what makes a session carry across arms — the model-switch
    pattern the experiments feature relies on — and it silences Agno's "multiple
    distinct databases share id" warning from building the same db twice."""
    if settings.database_url:
        from agno.db.postgres import PostgresDb

        # We ship psycopg v3 (psycopg[binary]) — NOT psycopg2. agno's PostgresDb
        # feeds the URL to SQLAlchemy, whose bare ``postgresql://`` dialect resolves
        # to psycopg2 and crashes with ModuleNotFoundError. Pin the v3 driver
        # explicitly (same conversion knowledge.py does for PgVector).
        db_url = settings.database_url
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return PostgresDb(db_url=db_url)

    # Local dev fallback — no external services required.
    from agno.db.sqlite import SqliteDb

    Path(settings.sqlite_file).parent.mkdir(parents=True, exist_ok=True)
    return SqliteDb(db_file=settings.sqlite_file)
