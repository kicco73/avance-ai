from ai.llm_provider import LLMProvider
from ai.anthropic_provider import AnthropicProvider
from ai.gemini_provider import GeminiProvider

_PROVIDERS = {
     "anthropic": AnthropicProvider,
     "gemini": GeminiProvider
}

def make(provider_name: str, api_key: str, model: str) -> LLMProvider:
    if provider_name not in _PROVIDERS:
        raise ValueError(f"Invalid provider name: {provider_name}. Must be one of: {', '.join(_PROVIDERS.keys())}")

    return _PROVIDERS[provider_name](api_key=api_key, model=model)
