from __future__ import annotations

from typing import AsyncIterator

import pytest

from ai.cascading_llm_provider import AutoTestLLMProvider
from ai.llm_provider import (
    AIServiceProviderPermanentError,
    AIServiceProviderRateLimitedError,
    AIServiceProviderUnavailableError,
)


class _FakeProvider:
    def __init__(self, chunks: list[str], error: BaseException | None = None) -> None:
        self._chunks = chunks
        self._error = error
        self.calls = 0

    async def generate_stream_with_schema(self, system_prompt: str, history: list[dict], schema: dict[str, str]) -> AsyncIterator[str]:
        self.calls += 1
        for chunk in self._chunks:
            yield chunk
        if self._error is not None:
            raise self._error

    def get_input_tokens(self, prompt: str) -> int:
        return 0

    def get_total_tokens(self) -> int:
        return 0


async def _drain(provider: AutoTestLLMProvider) -> list[str]:
    return [chunk async for chunk in provider.generate_stream_with_schema("prompt", [], {"text": "..."})]


@pytest.mark.asyncio
async def test_failure_before_any_output_cascades_to_next_provider() -> None:
    """A provider that fails before yielding anything has committed nothing
    to the caller yet, so retrying a different provider from scratch is safe."""
    broken = _FakeProvider([], error=AIServiceProviderRateLimitedError("rate limited"))
    healthy = _FakeProvider(["hello", " world"])
    provider = AutoTestLLMProvider([("broken", broken), ("healthy", healthy)])

    result = await _drain(provider)

    assert result == ["hello", " world"]
    assert broken.calls == 1
    assert healthy.calls == 1


@pytest.mark.asyncio
async def test_failure_after_partial_output_raises_instead_of_splicing_next_provider() -> None:
    """Regression test: once a provider has already streamed output for
    this call, a later failure must never fall through to a different
    provider — that provider's own from-scratch response would get
    concatenated onto what was already sent, corrupting the combined
    stream (e.g. two independent JSON documents glued together)."""
    broken = _FakeProvider(["partial "], error=AIServiceProviderRateLimitedError("rate limited mid-stream"))
    healthy = _FakeProvider(["should never be reached"])
    provider = AutoTestLLMProvider([("broken", broken), ("healthy", healthy)])

    chunks: list[str] = []
    with pytest.raises(AIServiceProviderRateLimitedError):
        async for chunk in provider.generate_stream_with_schema("prompt", [], {"text": "..."}):
            chunks.append(chunk)

    assert chunks == ["partial "]
    assert broken.calls == 1
    assert healthy.calls == 0


@pytest.mark.asyncio
async def test_every_provider_failing_before_output_raises_the_last_error() -> None:
    first = _FakeProvider([], error=AIServiceProviderRateLimitedError("first rate limited"))
    second = _FakeProvider([], error=AIServiceProviderPermanentError("second permanent error"))
    provider = AutoTestLLMProvider([("first", first), ("second", second)])

    with pytest.raises(AIServiceProviderPermanentError):
        await _drain(provider)

    assert first.calls == 1
    assert second.calls == 1


@pytest.mark.asyncio
async def test_unavailable_after_partial_output_also_raises_instead_of_advancing() -> None:
    """Same splicing risk applies to AIServiceProviderUnavailableError, not
    just rate limits/permanent errors — the `yielded` guard must cover all
    three failover error types."""
    broken = _FakeProvider(["partial "], error=AIServiceProviderUnavailableError("dropped mid-stream"))
    healthy = _FakeProvider(["should never be reached"])
    provider = AutoTestLLMProvider([("broken", broken), ("healthy", healthy)])

    chunks: list[str] = []
    with pytest.raises(AIServiceProviderUnavailableError):
        async for chunk in provider.generate_stream_with_schema("prompt", [], {"text": "..."}):
            chunks.append(chunk)

    assert chunks == ["partial "]
    assert healthy.calls == 0
