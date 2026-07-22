"""Prompt management — files load, substitute, and fail loudly when missing."""

import pytest

from agent_app.config import get_settings
from agent_app.context import build_context_block, build_context_message
from agent_app.prompt_loader import load_prompt


def test_load_prompt_substitutes_variables():
    text = load_prompt("assistant", agent_name="TestBot")
    assert "TestBot" in text
    assert "{agent_name}" not in text


def test_load_prompt_is_brace_safe():
    # Literal replacement: unknown {placeholders} and JSON braces survive as-is.
    text = load_prompt("assistant")
    assert "{agent_name}" in text  # no vars passed → placeholder untouched, no KeyError


def test_load_prompt_missing_raises_with_path_hint():
    with pytest.raises(FileNotFoundError, match="no_such_agent"):
        load_prompt("no_such_agent")


def test_context_message_is_volatile_channel_not_system():
    s = get_settings()
    msg = build_context_message(s, {"plan": "PME"})
    assert msg.role == "user"  # message-0, not the system prompt
    assert s.build_date in msg.content  # date lives here, not in the prefix
    assert "plan: PME" in build_context_block(s, {"plan": "PME"})
