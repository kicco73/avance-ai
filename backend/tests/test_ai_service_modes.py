"""AIServiceConfig.modes and AiService.for_live/for_test — each
classmethod filters the one incoming config list down to only the
entries relevant to its own cascade, building entirely independent
LLMProvider instances either way (see ai/ai_service.py's own docstrings).
"""
from __future__ import annotations

import pytest

from ai import AiService
from ai.llm_provider import AIServiceConfig

pytestmark = pytest.mark.contract


def _config(model: str, modes: tuple[str, ...] = ("live", "test")) -> AIServiceConfig:
    return AIServiceConfig(driver="gemini", model=model, key="fake-key", url=None, ui_label=model, modes=modes)


def _models(service: AiService) -> list[str]:
    return [m["model"] for m in service.get_models_info()["models"]]


class TestForLive:
    def test_includes_only_entries_whose_modes_contain_live(self):
        configs = [_config("both"), _config("live-only", modes=("live",)), _config("test-only", modes=("test",))]

        assert _models(AiService.for_live(configs)) == ["both", "live-only"]

    def test_defaults_to_including_an_entry_with_no_explicit_modes(self):
        assert _models(AiService.for_live([_config("default")])) == ["default"]

    def test_excludes_an_entry_with_an_empty_modes_tuple(self):
        # A lone entirely-excluded entry would leave the cascade with zero
        # providers, which ProviderCascade itself refuses to build at all
        # (a real misconfiguration AppConfig._parse_ai_services also
        # rejects at load time) — a sibling entry keeps this test's own
        # cascade non-empty so it's exercising the *filtering*, not that
        # unrelated guard.
        configs = [_config("hidden", modes=()), _config("visible")]
        assert _models(AiService.for_live(configs)) == ["visible"]

    def test_preserves_the_relative_order_of_the_surviving_entries(self):
        configs = [_config("a"), _config("skip", modes=("test",)), _config("b"), _config("c")]

        assert _models(AiService.for_live(configs)) == ["a", "b", "c"]


class TestForTest:
    def test_includes_only_entries_whose_modes_contain_test(self):
        configs = [_config("both"), _config("live-only", modes=("live",)), _config("test-only", modes=("test",))]

        assert _models(AiService.for_test(configs)) == ["both", "test-only"]

    def test_defaults_to_including_an_entry_with_no_explicit_modes(self):
        assert _models(AiService.for_test([_config("default")])) == ["default"]

    def test_excludes_an_entry_with_an_empty_modes_tuple(self):
        configs = [_config("hidden", modes=()), _config("visible")]
        assert _models(AiService.for_test(configs)) == ["visible"]


class TestLiveAndTestAreFullyIndependent:
    """No shared state whatsoever between the two — a real regression
    here would mean picking a live model quietly affects the test panel,
    or vice versa."""

    def test_selecting_a_model_on_one_never_affects_the_other(self):
        configs = [_config("a"), _config("b")]
        live = AiService.for_live(configs)
        test = AiService.for_test(configs)

        live.select_model(1)

        assert live.get_models_info()["auto"] is False
        assert live.get_models_info()["current_index"] == 1
        assert test.get_models_info()["auto"] is True
        assert test.get_models_info()["current_index"] == 0

    def test_a_provider_present_in_both_modes_is_still_two_distinct_provider_objects(self):
        """for_live/for_test each call _build_labeled_providers
        independently — even a shared config entry gets its own provider
        instance per cascade, never a reused/shared one."""
        configs = [_config("shared")]
        live = AiService.for_live(configs)
        test = AiService.for_test(configs)

        assert live._selectable_providers[0] is not test._selectable_providers[0]

    def test_filtering_never_mutates_the_shared_input_list(self):
        configs = [_config("a", modes=("live",)), _config("b", modes=("test",))]
        original = list(configs)

        AiService.for_live(configs)
        AiService.for_test(configs)

        assert configs == original


class TestNoAutoMode:
    """modes: [live, no-auto] (or [test, no-auto]) — still a normal entry
    in that mode's own manual selection list, but never chosen or
    failed over into by that mode's *auto* cascade."""

    def test_a_no_auto_entry_still_appears_in_the_manual_list(self):
        configs = [_config("normal", modes=("live",)), _config("manual-only", modes=("live", "no-auto"))]

        assert _models(AiService.for_live(configs)) == ["normal", "manual-only"]

    def test_auto_never_starts_on_a_no_auto_entry_even_when_it_is_first_in_the_list(self):
        configs = [_config("manual-only", modes=("live", "no-auto")), _config("normal", modes=("live",))]

        service = AiService.for_live(configs)
        info = service.get_models_info()

        assert info["auto"] is True
        assert info["models"][info["current_index"]]["model"] == "normal"

    def test_a_no_auto_entry_is_still_manually_selectable(self):
        configs = [_config("normal", modes=("live",)), _config("manual-only", modes=("live", "no-auto"))]
        service = AiService.for_live(configs)

        service.select_model(1)

        info = service.get_models_info()
        assert info["auto"] is False
        assert info["models"][info["current_index"]]["model"] == "manual-only"

    def test_a_simulated_failover_skips_straight_past_a_no_auto_entry(self):
        # Same pointer-advance a real failover triggers (see
        # AutoLiveLLMProvider.generate_stream_with_schema's own
        # self._cascade.advance() on a transient error) — the auto
        # cascade itself was never built with "manual-only" in it at all,
        # so there's no index it could ever land on.
        configs = [
            _config("primary", modes=("live",)),
            _config("manual-only", modes=("live", "no-auto")),
            _config("fallback", modes=("live",)),
        ]
        service = AiService.for_live(configs)

        service._auto_provider._cascade.advance()

        info = service.get_models_info()
        assert info["models"][info["current_index"]]["model"] == "fallback"

    def test_test_mode_honors_no_auto_the_same_way_as_live(self):
        configs = [_config("normal", modes=("test",)), _config("manual-only", modes=("test", "no-auto"))]

        service = AiService.for_test(configs)
        info = service.get_models_info()

        assert info["models"][info["current_index"]]["model"] == "normal"
