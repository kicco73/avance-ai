from __future__ import annotations
from ai.llm_provider import LLMProvider

_PROVIDERS = ("anthropic", "gemini")

def make(provider_name: str) -> LLMProvider:

    if provider_name == "anthropic":
        from ai.anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    if provider_name == "gemini":
        from ai.gemini_provider import GeminiProvider

        return GeminiProvider()

    raise RuntimeError(
        f"LLM_PROVIDER={provider_name!r} is not valid. Allowed values: {', '.join(_PROVIDERS)}. "
    )
