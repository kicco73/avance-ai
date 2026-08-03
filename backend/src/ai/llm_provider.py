from __future__ import annotations

import inspect
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from cascade import OnRetry, ProviderError, ProviderRateLimitedError, ProviderUnavailableError

logger = logging.getLogger(__name__)

# Called sync, fire-and-forget style — never awaited by a provider (see
# gemini_provider_v2.py's own generate/generate_stream) — once per
# metadata key ("audio", "signals", "env", ...), each key populated at
# most once per turn, in whatever order the provider itself resolves
# them. A caller that needs to await something in response (e.g. pushing
# a websocket frame) schedules its own asyncio.create_task, the same way
# chat.text_filter.StreamingTagFilter's own on_tag already does for the
# legacy tag-filtering path this replaces.
MetadataCallback = Callable[[str, Any], None]


def supports_on_metadata(bound_method: Callable) -> bool:
    """Whether `bound_method` (a provider's own generate/generate_stream)
    declares an `on_metadata` parameter — the one signal this codebase
    uses to tell a "v2" provider (real-time audio/signals/env callbacks,
    see gemini_provider_v2.py) apart from a "v1" one (plain text only;
    metadata is instead recovered by tag-filtering the raw reply — see
    chat.text_filter.ConcatTagFilter). A plain signature inspection
    rather than a marker base class/attribute: it reads directly off
    whatever a concrete provider actually implements, so a provider
    can't accidentally claim v2 support it doesn't really have."""
    try:
        params = inspect.signature(bound_method).parameters
    except (TypeError, ValueError):
        return False
    return "on_metadata" in params or any(p.kind == p.VAR_KEYWORD for p in params.values())

@dataclass(frozen=True)
class AIServiceConfig:
    driver: str
    model: str
    key: str
    url: str | None
    # Optional: falls back to `driver` (see AppConfig._parse_ai_services).
    ui_label: str
    ui_description: str | None = None


class AIServiceError(ProviderError):
    """Readable error to show on the frontend, without crashing the server."""
    message = "AI service error."


class AIServiceProviderUnavailableError(ProviderUnavailableError, AIServiceError):
    """Transient upstream overload (HTTP 503) — worth retrying."""
    message = "AI service unavailable after every retry."


class AIServiceProviderRateLimitedError(ProviderRateLimitedError, AIServiceError):
    """The upstream model API rejected the request for rate limiting (HTTP 429)."""
    message = "The AI service rate limit was exceeded."


def content_to_text(content: Any, provider_name: str = "LLM") -> str:
    """Flattens provider-neutral attachment blocks to plain text.
    Binary (base64) attachments are skipped if unsupported.
    """
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        source = block["source"]
        if source["type"] == "text":
            parts.append(f"[Attachment: {block['filename']}]\n{source['data']}")
        else:
            logger.warning(
                "Skipping unsupported binary attachment '%s' for %s.",
                block["filename"],
                provider_name,
            )
    return "\n\n".join(parts)


class LLMProvider(ABC):
    """Constructors are deliberately not part of this contract: a leaf
    implementation (see AnthropicProvider et al.) is built from one
    AIServiceConfig, while CascadingLLMProvider (ai/cascading_llm_provider.py)
    is built from an ordered list of LLMProviders — polymorphic dispatch
    only ever happens through generate()/generate_stream() below, never
    through __init__, so forcing one constructor shape on every
    implementation would only get in the way."""

    @abstractmethod
    def generate(
        self, system_prompt: str, history: list[dict], on_retry: OnRetry | None = None
    ) -> str:
        """Returns the complete reply text for `history` (list of {role, content}).
        Raises AIServiceError on failure, never an unhandled exception.
        `on_retry` is awaited before each backoff sleep — only ever called
        by a provider that itself retries (see CascadingLLMProvider); a
        leaf provider accepts it purely so callers can treat any
        LLMProvider uniformly, and simply never calls it.
        Not declared with an `on_metadata` parameter here — unlike
        generate_stream below, most concrete providers don't accept one at
        all (see supports_on_metadata/chat.turn_strategy_builder.
        build_turn_strategy, which decide whether it's even safe to pass
        one before ever calling this). A "v2" provider (see gemini_provider_v2.py) is free
        to add it as its own extra keyword-only parameter regardless — an
        ABC only enforces that the method exists, never that every
        override shares one exact signature."""
        raise NotImplementedError

    @abstractmethod
    async def generate_stream(
        self, system_prompt: str, history: list[dict], on_retry: OnRetry | None = None, on_metadata: MetadataCallback | None = None
    ) -> AsyncIterator[str]:
        """Yields response text chunks incrementally as they are generated by the model.
        Raises AIServiceError on failure, never an unhandled exception.
        See generate() above for `on_retry`. `on_metadata` is declared here
        (unlike generate()) purely for documentation/discoverability — a
        "v1" provider is free to ignore it in its own override, same
        reasoning as generate()'s own docstring."""
        raise NotImplementedError
        yield ""  # Satisfies type generators