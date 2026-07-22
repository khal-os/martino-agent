"""Shared test env — offline, no model calls, no external services."""

import os

os.environ.setdefault("MODEL_PROVIDER", "anthropic")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
os.environ.setdefault("LANGWATCH_ENABLED", "0")
os.environ.setdefault("KNOWLEDGE_ENABLED", "0")
