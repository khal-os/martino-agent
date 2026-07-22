"""Seed the PgVector knowledge base from local markdown.

Reads every ``*.md`` under ``src/agent_app/knowledge_base/`` and inserts it into
the vector store. Idempotent-ish: agno skips content whose hash already exists.

Usage:
    KNOWLEDGE_ENABLED=1 DATABASE_URL=... OPENAI_API_KEY=... python scripts/seed_knowledge.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_app.config import get_settings
from agent_app.knowledge import build_knowledge


def main() -> int:
    settings = get_settings()
    if not settings.knowledge_enabled:
        print("KNOWLEDGE_ENABLED is off — nothing to seed. Set KNOWLEDGE_ENABLED=1.")
        return 1

    knowledge = build_knowledge(settings)
    if knowledge is None:
        raise RuntimeError("build_knowledge() returned None despite KNOWLEDGE_ENABLED=1.")

    files = sorted(settings.knowledge_dir.glob("*.md"))
    if not files:
        print(f"No markdown found in {settings.knowledge_dir}")
        return 1

    for path in files:
        print(f"→ inserting {path.name}")
        # agno's Knowledge accepts file paths / text / urls depending on version;
        # `path=` is the stable way to ingest a local file.
        knowledge.insert(path=str(path))

    print(f"Seeded {len(files)} document(s) into table '{settings.knowledge_table}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
