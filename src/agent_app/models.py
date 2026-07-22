"""Model factory — one place to swap LLM providers via env.

Namastex agents typically run against a proxy (LiteLLM at ``llm.khal.ai`` or
OpenRouter) so keys/spend are centralized, with Anthropic Claude as the default
brain. This factory keeps the provider choice out of the agent definition.

⚠️ Model IDs age fast. Before pinning one, list the provider's live catalog:
    curl api.anthropic.com/v1/models -H "x-api-key: $KEY" -H "anthropic-version: 2023-06-01"
    curl api.openai.com/v1/models -H "Authorization: Bearer $KEY"
    curl "generativelanguage.googleapis.com/v1beta/models?key=$KEY"

Docs — Agno models & building agents: https://docs.agno.com/agents/building-agents
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import Settings, require_env

# ─── Model tier map ──────────────────────────────────────────────────────────
# Every provider ships three durable capability tiers. Use "@fast" / "@medium" /
# "@high" as MODEL_ID (or FALLBACK_MODEL_ID) and the factory resolves it here.
#
#   fast   → cheap, low-latency; wizards, classification, housekeeping
#   medium → the daily driver; most agent turns
#   high   → hard reasoning, long context, judge-of-last-resort
#
# ⚠️ VERIFIED against the live provider APIs on TIERS_VERIFIED_ON. IDs age fast:
#    re-check the /models endpoints (see module docstring) and bump this map —
#    don't trust memory or docs.
TIERS_VERIFIED_ON = "2026-07-14"
MODEL_TIERS: dict[str, dict[str, str]] = {
    "anthropic": {
        "fast": "claude-haiku-4-5-20251001",
        "medium": "claude-sonnet-5",
        "high": "claude-opus-4-8",
    },
    "openai": {  # GPT-5.6 family: Luna/Terra/Sol are official tier names (2026-07-09)
        "fast": "gpt-5.6-luna",
        "medium": "gpt-5.6-terra",
        "high": "gpt-5.6-sol",
    },
    "google": {
        "fast": "gemini-3.1-flash-lite",
        "medium": "gemini-3.5-flash",
        "high": "gemini-3.1-pro-preview",  # newest pro line (preview); stable: gemini-2.5-pro
    },
}
MODEL_TIERS["gemini"] = MODEL_TIERS["google"]


def model_for_tier(provider: str, tier: str) -> str:
    """Resolve a tier alias ('fast'|'medium'|'high') to the provider's current model id."""
    provider_map = MODEL_TIERS.get(provider.lower())
    if provider_map is None:
        raise ValueError(
            f"No tier map for provider {provider!r} (have: {sorted(set(MODEL_TIERS))}). "
            f"Pass an explicit model id instead."
        )
    model_id = provider_map.get(tier.lower())
    if model_id is None:
        raise ValueError(f"Unknown tier {tier!r} — use one of: fast, medium, high")
    return model_id


# ─── Provider builders ───────────────────────────────────────────────────────
# One builder per provider, registered in PROVIDERS below. Imports stay lazy
# (only the provider you use gets imported).
# Signature: (model_id, api_key, settings) -> agno model instance.
ModelBuilder = Callable[[str, str, Settings], Any]


def _build_anthropic(model_id: str, key: str, settings: Settings) -> Any:
    from agno.models.anthropic import Claude

    # Prompt caching: cache the static system prompt so the large stable prefix
    # isn't re-billed every turn. Requires the prefix to be byte-stable — see
    # agents/assistant.py (add_datetime_to_context=False, volatile context
    # injected via additional_input, not the system prompt).
    return Claude(
        id=model_id,
        api_key=key,
        cache_system_prompt=settings.prompt_cache,
        extended_cache_time=settings.cache_extended_ttl,  # 1h TTL vs default 5min
    )


def _build_openai(model_id: str, key: str, settings: Settings) -> Any:
    from agno.models.openai import OpenAIChat

    return OpenAIChat(id=model_id, api_key=key)


def _build_google(model_id: str, key: str, settings: Settings) -> Any:
    from agno.models.google import Gemini

    return Gemini(id=model_id, api_key=key)


def _build_openrouter(model_id: str, key: str, settings: Settings) -> Any:
    from agno.models.openrouter import OpenRouter

    return OpenRouter(id=model_id, api_key=key)


def _build_litellm(model_id: str, key: str, settings: Settings) -> Any:
    # LiteLLM proxy exposes an OpenAI-compatible API (base_url required).
    from agno.models.litellm import LiteLLMOpenAI

    return LiteLLMOpenAI(
        id=model_id,
        api_key=key,
        base_url=settings.model_base_url or "https://llm.khal.ai",
    )


# ─── Provider registry ───────────────────────────────────────────────────────
# One entry per provider = its model builder + the env var holding its API key,
# co-located so adding a provider is a SINGLE edit here (add a MODEL_TIERS entry
# too if you want @alias support). `litellm` uses the generic MODEL_API_KEY.
@dataclass(frozen=True)
class Provider:
    build: ModelBuilder  # (model_id, api_key, settings) -> agno model
    key_env: str  # env var holding this provider's API key


PROVIDERS: dict[str, Provider] = {
    "anthropic": Provider(_build_anthropic, "ANTHROPIC_API_KEY"),
    "openai": Provider(_build_openai, "OPENAI_API_KEY"),
    "google": Provider(_build_google, "GOOGLE_API_KEY"),
    "gemini": Provider(_build_google, "GOOGLE_API_KEY"),  # alias
    "openrouter": Provider(_build_openrouter, "OPENROUTER_API_KEY"),
    "litellm": Provider(_build_litellm, "MODEL_API_KEY"),
}


def resolve_api_key(settings: Settings, provider: str) -> str:
    """Resolve the API key for a provider, failing fast when unset.

    An explicit ``MODEL_API_KEY`` wins for the primary provider; otherwise fall
    back to the provider-specific env var (e.g. ``ANTHROPIC_API_KEY``).
    """
    if settings.model_api_key and provider == settings.model_provider:
        return settings.model_api_key
    entry = PROVIDERS.get(provider)
    return require_env(entry.key_env if entry else "MODEL_API_KEY")


def build_model(
    settings: Settings,
    provider: str | None = None,
    model_id: str | None = None,
) -> Any:
    """Return an Agno model instance.

    With no overrides this builds the primary model (``MODEL_PROVIDER``/``MODEL_ID``).
    Pass ``provider``/``model_id`` to build alternates (fallbacks, judges, workers).

    ``model_id`` accepts tier aliases: ``@fast`` / ``@medium`` / ``@high`` are
    resolved via ``MODEL_TIERS`` for the provider (e.g. MODEL_ID=@fast).
    """
    provider = (provider or settings.model_provider).lower()
    model_id = model_id or settings.model_id
    if model_id.startswith("@"):
        model_id = model_for_tier(provider, model_id[1:])

    entry = PROVIDERS.get(provider)
    if entry is None:
        raise ValueError(f"Unknown model provider: {provider!r} (have: {sorted(PROVIDERS)})")
    return entry.build(model_id, resolve_api_key(settings, provider), settings)


def build_fallback_config(settings: Settings) -> Any | None:
    """Model fallback (eugenia pattern): primary fails → retry on a second model.

    Wires the fallback into all three Agno triggers:
      - ``on_error``            — provider outage: 5xx / network errors
      - ``on_rate_limit``       — 429/529
      - ``on_context_overflow`` — prompt too large for the primary's window

    ⚠️ By design, Agno does NOT fall back on other 4xx errors (bad model id,
    malformed request, auth): those are caller bugs — retrying them on another
    model would just mask the bug and double the bill.

    Returns None when no ``FALLBACK_MODEL_ID`` is configured (feature off).
    Cross-provider fallback (e.g. anthropic → openai) is the resilient choice:
    it survives a whole-provider outage, not just one model's bad day.
    """
    if not settings.fallback_model_id:
        return None

    from agno.models.fallback import FallbackConfig

    fallback = build_model(
        settings,
        provider=settings.fallback_provider or settings.model_provider,
        model_id=settings.fallback_model_id,
    )
    return FallbackConfig(
        on_error=[fallback],
        on_rate_limit=[fallback],
        on_context_overflow=[fallback],
    )
