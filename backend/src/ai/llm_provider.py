from __future__ import annotations

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

# This application's own structured-metadata contract — every "v2"
# provider's own build_schema() (see LLMProvider.build_schema below) is
# wired up with exactly these fields, both when actually configuring one
# (see AiService._build_provider) and when merely probing whether it
# supports this at all (see supports_structured_metadata) — the two
# calls share the same field definitions on purpose, so probing is a
# harmless, idempotent no-op wherever the provider's already configured.
# "audio" is a priority tag (reported *before* 'text' — see
# LLMProvider.build_schema's own docstring), "signals"/"env" are not
# (reported after, and optional/nullable).

METADATA_PRIORITY_TAGS: dict[str, tuple[type, str]] = {
    "audio": (str, "Short textual version for text-to-speech. Generated first."),
}

METADATA_TAGS: dict[str, tuple[type, str]] = {
    "env": (str, "Updated memory state. Include all current context keys in the form key: value, one per line"),
    "signals": (str, "JSON dictionary containing required calculated signal values."),
}


def supports_structured_metadata(provider: LLMProvider) -> bool:
    """Whether `provider` actually supports reporting audio/signals/env
    as structured metadata (see gemini_provider_v2.py's own build_schema)
    rather than embedded [audio]/[signals]/[env] tags in plain text (see
    chat.text_filter.ConcatTagFilter) — probed by calling build_schema
    itself with this module's own METADATA_PRIORITY_TAGS/METADATA_TAGS
    and watching whether it raises NotImplementedError (LLMProvider's own
    default body below, never overridden by a "v1" provider) rather than
    inspecting generate()/generate_stream()'s own signatures separately:
    build_schema succeeding or not is the single source of truth for
    both, since a "v2" provider (see gemini_provider_v2.py) always
    accepts on_metadata on either once it's configured at all."""
    try:
        provider.build_schema(METADATA_PRIORITY_TAGS, METADATA_TAGS)
    except NotImplementedError:
        return False
    return True

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

    async def generate(
        self, system_prompt: str, history: list[dict], on_retry: OnRetry | None = None, on_metadata: MetadataCallback | None = None
    ) -> str:
        """Returns the complete reply text for `history` (list of {role, content}),
        built by collecting every chunk generate_stream() below yields and
        joining them — every concrete provider today has real streaming
        support (see AnthropicProvider/OpenAIProvider/gemini_provider(_v2).py),
        so there's no separate non-streaming call left to hand-maintain
        per provider; this one, shared implementation is enough. Not
        declared abstract: a provider is free to override this directly
        if it ever has a genuinely cheaper way to get a single blocking
        reply, but none does today. Raises AIServiceError on failure,
        never an unhandled exception — generate_stream's own docstring
        covers this; whatever it raises propagates through unchanged."""
        chunks: list[str] = []
        async for chunk in self.generate_stream(system_prompt, history, on_retry=on_retry, on_metadata=on_metadata):
            chunks.append(chunk)
        return "".join(chunks)

    @abstractmethod
    async def generate_stream(
        self, system_prompt: str, history: list[dict], on_retry: OnRetry | None = None, on_metadata: MetadataCallback | None = None
    ) -> AsyncIterator[str]:
        """Yields response text chunks incrementally as they are generated
        by the model — the one method every concrete provider must
        actually implement (see generate() above, built entirely on top
        of this). Raises AIServiceError on failure, never an unhandled
        exception. `on_retry` is awaited before each backoff sleep — only
        ever called by a provider that itself retries (see
        CascadingLLMProvider); a leaf provider accepts it purely so
        callers can treat any LLMProvider uniformly, and simply never
        calls it. `on_metadata` is accepted by every concrete provider
        (even a "v1" one, which simply never calls it) precisely so
        generate()'s own shared default above can always forward it
        without checking first — see ai.llm_provider.
        supports_structured_metadata for the real capability signal."""
        raise NotImplementedError
        yield ""  # Satisfies type generators

    def build_schema(self, priority_tags: dict[str, tuple[type, str]], tags: dict[str, tuple[type, str]]) -> dict:
        """Configures this provider to report the given fields as
        structured metadata alongside its own reply — e.g. audio/signals/
        env, see METADATA_PRIORITY_TAGS/METADATA_TAGS above — instead of
        via embedded [audio]/[signals]/[env] tags in plain text (see
        chat.text_filter.ConcatTagFilter). Not declared abstract: the
        default here (never overridden) is itself the "v1" contract —
        raising NotImplementedError is exactly how supports_structured_metadata
        above tells a "v1" provider apart from a "v2" one (see
        gemini_provider_v2.py's own override) — and how
        AiService._build_provider knows there's simply nothing to wire up
        for a provider that doesn't support this at all."""
        raise NotImplementedError
