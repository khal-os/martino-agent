"""Example tools — copy these as the shape for real tools.

House rule (eugenia/renan): **the LLM decides *what*, code decides *how*.** Tools
are deterministic Python functions that do the real work and return a small,
structured result. Keep secrets, retries, ID cascades and payload-building in
code — never ask the model to hand-craft an API call.

A tool can:
  * take plain typed args (the model fills them in),
  * read/write ``run_context.session_state`` for cross-turn continuity,
  * use the ``@tool`` decorator to attach per-tool pre/post hooks or metadata.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agno.run import RunContext
from agno.tools import tool

# Toy price catalog — a real tool would hit a DB/API here.
_PRICE_CATALOG = {"milk": "R$4.50", "bread": "R$6.00"}


def current_time() -> str:
    """Return the current UTC time in ISO-8601. Use when the user asks 'what time is it'."""
    return datetime.now(UTC).isoformat()


def add_to_cart(run_context: RunContext, item: str) -> str:
    """Add an item to the user's cart (persisted in session state across turns).

    Args:
        item: The product name to add.
    """
    # session_state is auto-loaded from the db for this session_id and auto-persisted
    # after the run — no manual save needed.
    cart = run_context.session_state.setdefault("cart", [])
    cart.append(item)
    return f"Added '{item}'. Cart now has {len(cart)} item(s): {', '.join(cart)}."


def view_cart(run_context: RunContext) -> str:
    """Show the items currently in the user's cart."""
    cart = run_context.session_state.get("cart", [])
    if not cart:
        return "The cart is empty."
    return "Cart: " + ", ".join(cart)


@tool(name="reverse_text", description="Reverse a string. Demonstrates the @tool decorator.")  # type: ignore[untyped-decorator]  # agno's @tool is untyped
def reverse_text(text: str) -> str:
    return text[::-1]


def lookup_price(item: str) -> str:
    """Look up a product's price. Demonstrates a CUSTOM LangWatch span.

    The Agno instrumentor already traces the tool call itself. Add a custom span
    around a *sub-step* (e.g. an external API call, a DB query, a retrieval) when
    you want its own timing/input/output in the trace tree.
    """
    try:
        import langwatch

        # `type` ∈ llm | rag | tool | span | ... ; shows up as a node in the trace.
        # ignore_missing_trace_warning: stay quiet when called outside a trace (tests).
        with langwatch.span(
            type="tool", name="price-catalog-lookup", ignore_missing_trace_warning=True
        ) as span:
            span.set_input({"item": item})
            price = _PRICE_CATALOG.get(item.lower(), "unknown")
            span.set_output({"price": price})
            return f"{item}: {price}"
    except Exception:  # noqa: BLE001 — telemetry must never break the tool
        price = _PRICE_CATALOG.get(item.lower(), "unknown")
        return f"{item}: {price}"


# Export the toolset the agent gets. Real projects add API-backed tools here.
EXAMPLE_TOOLS = [current_time, add_to_cart, view_cart, reverse_text, lookup_price]
