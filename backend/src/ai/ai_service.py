from __future__ import annotations

from typing import AsyncIterator, Sequence
from cascade import OnRetry
from ai.llm_provider import LLMProvider, AIServiceConfig
from ai.cascading_llm_provider import CascadingLLMProvider
from ai.anthropic_provider import AnthropicProvider
from ai.gemini_provider import GeminiProvider
from ai.openai_provider import OpenAIProvider

_PROVIDER_CLASSES = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
}


class AiService(object):
    """Thin wrapper that always talks to a plain LLMProvider — generate()/
    generate_stream() never know or care whether that's a single concrete
    provider or a CascadingLLMProvider fronting several (see
    cascading_llm_provider.py). `from_config` is the usual entry point
    (see main.py); the plain constructor is what makes that substitution
    possible.

    On top of that substitutability, this class adds one application-level
    feature: which LLMProvider is "active" is itself selectable at
    runtime (see select_model) — auto (the default) uses `auto_provider`
    (normally the cascade, for its retry/fallback behavior); an explicit
    choice pins `generate`/`generate_stream` to one of `selectable_providers`
    instead, bypassing the fallback-to-a-different-model behavior until
    auto is re-selected. Either way, the active provider is still just an
    LLMProvider — this selection logic lives entirely here, invisible to
    ChatService and everything upstream of it.

    `selectable_providers` are each a one-element CascadingLLMProvider, not
    the bare leaf: a leaf's own generate()/generate_stream() are written
    against LLMProvider's contract, which async-wise only CascadingLLMProvider
    actually honors (see cascading_llm_provider.py and llm_provider.py's
    LLMProvider) — a leaf's generate() is plain sync, called through
    ProviderCascade's own asyncio.to_thread. Wrapping keeps that detail out
    of _active_provider, and as a side benefit still retries a single
    explicitly-pinned model with backoff before giving up.
    """

    def __init__(
        self,
        auto_provider: LLMProvider,
        selectable_providers: Sequence[LLMProvider] | None = None,
        configs: list[AIServiceConfig] | None = None,
    ) -> None:
        self._auto_provider = auto_provider
        # Only present when built via from_config() below — index-aligned
        # with `configs`, and both empty for a hand-built AiService that
        # only ever runs in auto mode (select_model has nothing to pick).
        self._selectable_providers = selectable_providers or []
        self._configs = configs or []
        # None = auto (use auto_provider); an index pins to that entry of
        # selectable_providers/configs instead. In-memory only, like the
        # rest of this prototype's mutable state (e.g. auto_tracking_enabled).
        self._selected_index: int | None = None

    @classmethod
    def from_config(cls, ai_service_config: list[AIServiceConfig]) -> "AiService":
        labeled = [
            (f"{service.driver}/{service.model}", cls._build_provider(service))
            for service in ai_service_config
        ]
        selectable = [CascadingLLMProvider([entry]) for entry in labeled]
        return cls(CascadingLLMProvider(labeled), selectable_providers=selectable, configs=ai_service_config)

    @staticmethod
    def _build_provider(service: AIServiceConfig) -> LLMProvider:
        if service.driver not in _PROVIDER_CLASSES:
            raise ValueError(
                f"Invalid provider driver: {service.driver!r}. Must be one of: "
                f"{', '.join(_PROVIDER_CLASSES.keys())}"
            )
        return _PROVIDER_CLASSES[service.driver](service)

    @property
    def _active_provider(self) -> LLMProvider:
        if self._selected_index is None:
            return self._auto_provider
        return self._selectable_providers[self._selected_index]

    def select_model(self, index: int | None) -> None:
        """`index=None` selects auto (the cascade's own retry/fallback
        order); an int pins generate()/generate_stream() to that single
        configured model directly. Raises ValueError for an out-of-range
        index."""
        if index is not None and not (0 <= index < len(self._selectable_providers)):
            raise ValueError(f"Invalid model index: {index!r}.")
        self._selected_index = index

    def get_models_info(self) -> dict:
        """The full ai-service provider roster, whether auto mode is on,
        and which model is in effect right now either way — for the
        frontend's model menu (see controller.py's GET/POST
        /api/ai/models*). `current_index` is the cascade's own current
        entry while in auto mode (falls back to 0 if the auto provider
        isn't a CascadingLLMProvider), or the explicit selection otherwise."""
        auto = self._selected_index is None
        current_index = (
            getattr(self._auto_provider, "current_index", 0) if auto else self._selected_index
        )
        return {
            "auto": auto,
            "current_index": current_index,
            "models": [
                {
                    "driver": c.driver,
                    "model": c.model,
                    "url": c.url,
                    "ui_label": c.ui_label,
                    "ui_description": c.ui_description,
                }
                for c in self._configs
            ],
        }

    async def generate(
        self,
        system_prompt: str,
        history: list[dict],
        on_retry: OnRetry | None = None,
    ) -> str:
        """Reply text for `history` (list of {role, content}). Raises
        AIServiceError if the active provider fails."""
        return await self._active_provider.generate(system_prompt, history, on_retry=on_retry)

    async def generate_stream(
        self,
        system_prompt: str,
        history: list[dict],
        on_retry: OnRetry | None = None,
    ) -> AsyncIterator[str]:
        """Yields reply chunks incrementally for `history` (list of {role, content}).
        Raises AIServiceError if the active provider fails."""
        async for chunk in self._active_provider.generate_stream(system_prompt, history, on_retry=on_retry):
            yield chunk
