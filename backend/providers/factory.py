"""Selects which LLM provider to use, based on `LLM_PROVIDER` in .env."""
from __future__ import annotations

import os

from llm_provider import LLMProvider

_PROVIDERS = ("anthropic", "gemini")


def build_provider() -> LLMProvider:
    """Instantiates the provider configured via `LLM_PROVIDER`.

    Must be called exactly once at server startup: a missing or unrecognized
    value must fail startup explicitly, not the first chat message.
    """
    provider_name = os.environ.get("LLM_PROVIDER", "").strip().lower()

    if provider_name == "anthropic":
        from providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    if provider_name == "gemini":
        from providers.gemini_provider import GeminiProvider

        return GeminiProvider()

    raise RuntimeError(
        f"LLM_PROVIDER={provider_name!r} is not valid. Allowed values: {', '.join(_PROVIDERS)}. "
        "Set it in .env."
    )
