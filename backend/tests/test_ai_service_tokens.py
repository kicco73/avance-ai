from __future__ import annotations

from typing import AsyncIterator

import pytest

from ai import AiService
from ai._providers.cascading_llm_provider import AutoLiveLLMProvider
from ai.llm_provider import AIServiceProviderRateLimitedError

pytestmark = pytest.mark.contract


class _FakeProvider:
    """Minimal stand-in for an LLMProvider: reports a fixed total, and
    fails the way a throttled provider does — before yielding anything,
    which is what makes the live cascade fail over to the next one."""

    def __init__(self, tokens: int) -> None:
        self._tokens = tokens
        self._chunks: list[str] = []

    async def generate_stream_with_schema(
        self, system_prompt: str, history: list[dict], schema: dict[str, str], on_metadata=None,
    ) -> AsyncIterator[str]:
        for chunk in self._chunks:
            yield chunk
        raise AIServiceProviderRateLimitedError("rate limited")

    def get_total_tokens(self) -> int:
        return self._tokens


class TestAutoLiveLLMProviderGetTotalTokens:

    def test_sums_every_wrapped_providers_own_total(self):
        cascade = AutoLiveLLMProvider([("a", _FakeProvider(10)), ("b", _FakeProvider(5))])
        assert cascade.get_total_tokens() == 15

    async def test_counts_a_provider_the_cascade_already_advanced_past(self):
        cascade = AutoLiveLLMProvider([("a", _FakeProvider(10)), ("b", _FakeProvider(5))])

        with pytest.raises(AIServiceProviderRateLimitedError):
            async for _ in cascade.generate_stream_with_schema("sys", [], {"text": "t"}):
                pass

        assert cascade.current_index == 1
        assert cascade.get_total_tokens() == 15


class TestAiServiceGetTotalTokens:

    def _service(self) -> tuple[AiService, _FakeProvider, _FakeProvider]:
        provider_a = _FakeProvider(11)
        provider_b = _FakeProvider(22)
        auto_provider = AutoLiveLLMProvider([("a", provider_a), ("b", provider_b)])
        selectable = [
            AutoLiveLLMProvider([("a", provider_a)]),
            AutoLiveLLMProvider([("b", provider_b)]),
        ]
        return AiService(auto_provider, selectable_providers=selectable), provider_a, provider_b

    def test_auto_mode_delegates_to_the_cascade_over_every_provider(self):
        ai_service, _, _ = self._service()
        assert ai_service.get_total_tokens() == 33

    def test_pinned_mode_delegates_to_only_the_selected_provider(self):
        ai_service, _, _ = self._service()
        ai_service.select_model(1)
        assert ai_service.get_total_tokens() == 22
