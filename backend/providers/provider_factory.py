from __future__ import annotations
from llm_provider import LLMProvider

_PROVIDERS = ("anthropic", "gemini")

def make(provider_name: str) -> LLMProvider:

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
