"""Tools are deterministic functions — test them directly, no LLM involved."""

from agno.run import RunContext

from agent_app.tools.example_tools import add_to_cart, current_time, lookup_price, view_cart


def make_ctx() -> RunContext:
    return RunContext(run_id="test-run", session_id="test-session", session_state={})


def test_add_to_cart_persists_in_session_state():
    ctx = make_ctx()
    out = add_to_cart(ctx, "milk")
    assert "milk" in out
    assert ctx.session_state["cart"] == ["milk"]

    add_to_cart(ctx, "bread")
    assert ctx.session_state["cart"] == ["milk", "bread"]


def test_view_cart_empty_and_filled():
    ctx = make_ctx()
    assert "empty" in view_cart(ctx).lower()
    add_to_cart(ctx, "milk")
    assert "milk" in view_cart(ctx)


def test_current_time_is_iso():
    from datetime import datetime

    datetime.fromisoformat(current_time())  # raises if not ISO


def test_lookup_price_known_and_unknown():
    assert "R$4.50" in lookup_price("Milk")
    assert "unknown" in lookup_price("caviar")


def test_reverse_text_tool_decorator():
    from agent_app.tools.example_tools import reverse_text

    # @tool wraps the function into an agno Function; the original callable
    # lives on .entrypoint.
    assert reverse_text.entrypoint("abc") == "cba"
    assert reverse_text.name == "reverse_text"
