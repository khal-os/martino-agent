"""Shared test env — offline, no model calls, no external services."""

import os

os.environ.setdefault("MODEL_PROVIDER", "anthropic")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
os.environ.pop("CONNECTOR_CATALOG_URL", None)  # tracing off — no catalog in tests
os.environ.pop("CONNECTOR_REGISTER_URL", None)  # legacy spelling — also off
os.environ.setdefault("KNOWLEDGE_ENABLED", "0")
