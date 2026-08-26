from __future__ import annotations

import pytest

from ai.ai_service import AiService
from ai.cascading_llm_provider import CascadingLLMProvider

pytestmark = pytest.mark.contract


class _FakeProvider:
    """Minimal stand-in for an LLMProvider — only get_total_tokens() is
    exercised here, so nothing else needs implementing."""

    def __init__(self, tokens: int) -> None:
        self._tokens = tokens

    def get_total_tokens(self) -> int:
        return self._tokens


class TestCascadingLLMProviderGetTotalTokens:

    def test_sums_every_wrapped_providers_own_total(self):
        cascade = CascadingLLMProvider([("a", _FakeProvider(10)), ("b", _FakeProvider(5))])
        assert cascade.get_total_tokens() == 15

    def test_counts_a_provider_the_cascade_already_advanced_past(self):
        # A cascade that failed over from "a" to "b" must still count
        # whatever "a" already burned before the fallback kicked in.
        cascade = CascadingLLMProvider([("a", _FakeProvider(10)), ("b", _FakeProvider(5))])
        cascade._cascade.advance()
        assert cascade.current_index == 1
        assert cascade.get_total_tokens() == 15


class TestAiServiceGetTotalTokens:

    def _service(self) -> tuple[AiService, _FakeProvider, _FakeProvider]:
        provider_a = _FakeProvider(11)
        provider_b = _FakeProvider(22)
        auto_provider = CascadingLLMProvider([("a", provider_a), ("b", provider_b)])
        selectable = [
            CascadingLLMProvider([("a", provider_a)]),
            CascadingLLMProvider([("b", provider_b)]),
        ]
        return AiService(auto_provider, selectable_providers=selectable), provider_a, provider_b

    def test_auto_mode_delegates_to_the_cascade_over_every_provider(self):
        ai_service, _, _ = self._service()
        assert ai_service.get_total_tokens() == 33

    def test_pinned_mode_delegates_to_only_the_selected_provider(self):
        ai_service, _, _ = self._service()
        ai_service.select_model(1)
        assert ai_service.get_total_tokens() == 22
