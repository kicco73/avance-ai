from __future__ import annotations

from typing import AsyncIterator

import pytest

from ai._providers.cascading_llm_provider import AutoTestLLMProvider
from ai.llm_provider import (
    AIServiceProviderPermanentError,
    AIServiceProviderRateLimitedError,
    AIServiceProviderUnavailableError,
)
from try_again_error import TryAgainError


class _FakeProvider:
    def __init__(self, chunks: list[str], error: BaseException | None = None) -> None:
        self._chunks = chunks
        self._error = error
        self.calls = 0

    async def generate_stream_with_schema(
        self, system_prompt: str, history: list[dict], schema: dict[str, str], on_metadata=None,
    ) -> AsyncIterator[str]:
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


def test_transient_errors_are_try_again_errors_but_permanent_is_not() -> None:
    assert isinstance(AIServiceProviderRateLimitedError("x"), TryAgainError)
    assert isinstance(AIServiceProviderUnavailableError("x"), TryAgainError)
    assert not isinstance(AIServiceProviderPermanentError("x"), TryAgainError)


@pytest.mark.asyncio
async def test_a_failure_before_any_output_cascades_to_the_next_provider() -> None:
    """A provider that fails before yielding anything has committed nothing
    to the caller yet, so retrying a different provider from scratch is safe."""
    broken = _FakeProvider([], error=AIServiceProviderRateLimitedError("rate limited"))
    healthy = _FakeProvider(["hello", " world"])

    result = await _drain(AutoTestLLMProvider([("broken", broken), ("healthy", healthy)]))

    assert result == ["hello", " world"]
    assert broken.calls == 1
    assert healthy.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    AIServiceProviderRateLimitedError("rate limited mid-stream"),
    AIServiceProviderUnavailableError("dropped mid-stream"),
], ids=["rate-limited", "unavailable"])
async def test_a_failure_after_partial_output_raises_instead_of_splicing_in_the_next_provider(error) -> None:
    """Regression test: once a provider has already streamed output for
    this call, a later failure must never fall through to a different
    provider — that provider's own from-scratch response would get
    concatenated onto what was already sent, corrupting the combined
    stream (e.g. two independent JSON documents glued together). The
    `yielded` guard must cover every failover error type."""
    broken = _FakeProvider(["partial "], error=error)
    healthy = _FakeProvider(["should never be reached"])
    provider = AutoTestLLMProvider([("broken", broken), ("healthy", healthy)])

    chunks: list[str] = []
    with pytest.raises(type(error)):
        async for chunk in provider.generate_stream_with_schema("prompt", [], {"text": "..."}):
            chunks.append(chunk)

    assert chunks == ["partial "]
    assert broken.calls == 1
    assert healthy.calls == 0


@pytest.mark.asyncio
async def test_an_exhausted_cascade_raises_the_last_error_as_permanent_never_as_try_again() -> None:
    """AIServiceProviderRateLimitedError/UnavailableError are TryAgainError
    so a job can reschedule itself on a transient failure — but only while
    the cascade still had another provider left to try. Once every
    provider has been tried once, there is no "next" left within this
    call: reporting that as TryAgainError too would let a job reschedule
    itself forever, re-hitting the same exhausted cascade every time."""
    first = _FakeProvider([], error=AIServiceProviderRateLimitedError("first rate limited"))
    second = _FakeProvider([], error=AIServiceProviderPermanentError("second permanent error"))
    with pytest.raises(AIServiceProviderPermanentError):
        await _drain(AutoTestLLMProvider([("first", first), ("second", second)]))
    assert first.calls == 1
    assert second.calls == 1

    all_rate_limited = AutoTestLLMProvider([
        ("first", _FakeProvider([], error=AIServiceProviderRateLimitedError("first rate limited"))),
        ("second", _FakeProvider([], error=AIServiceProviderRateLimitedError("second rate limited"))),
    ])
    with pytest.raises(AIServiceProviderPermanentError) as excinfo:
        await _drain(all_rate_limited)

    assert not isinstance(excinfo.value, TryAgainError)
    assert isinstance(excinfo.value.__cause__, AIServiceProviderRateLimitedError)
