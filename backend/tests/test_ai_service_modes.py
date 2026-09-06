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


@pytest.mark.parametrize("mode", ["live", "test"])
def test_each_cascade_keeps_only_the_entries_declaring_its_own_mode_in_their_original_order(mode):
    """A lone entirely-excluded entry would leave the cascade with zero
    providers, which ProviderCascade itself refuses to build at all (a
    real misconfiguration AppConfig._parse_ai_services also rejects at
    load time) — a sibling keeps this exercising the *filtering*."""
    build = AiService.for_live if mode == "live" else AiService.for_test
    other = "test" if mode == "live" else "live"

    configs = [_config("both"), _config(f"{mode}-only", modes=(mode,)), _config(f"{other}-only", modes=(other,))]
    assert _models(build(configs)) == ["both", f"{mode}-only"]

    assert _models(build([_config("default")])) == ["default"]
    assert _models(build([_config("hidden", modes=()), _config("visible")])) == ["visible"]

    ordered = [_config("a"), _config("skip", modes=(other,)), _config("b"), _config("c")]
    assert _models(build(ordered)) == ["a", "b", "c"]


class TestLiveAndTestAreFullyIndependent:
    """No shared state whatsoever between the two — a real regression
    here would mean picking a live model quietly affects the test panel,
    or vice versa."""

    def test_selecting_a_model_on_one_never_affects_the_other_and_each_builds_its_own_provider_objects(self):
        """for_live/for_test each call _build_labeled_providers
        independently — even a shared config entry gets its own provider
        instance per cascade, never a reused/shared one."""
        configs = [_config("a"), _config("b")]
        live = AiService.for_live(configs)
        test = AiService.for_test(configs)

        live.select_model(1)

        assert live.get_models_info()["auto"] is False
        assert live.get_models_info()["current_index"] == 1
        assert test.get_models_info()["auto"] is True
        assert test.get_models_info()["current_index"] == 0
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

    @pytest.mark.parametrize("mode", ["live", "test"])
    def test_a_no_auto_entry_is_listed_and_manually_selectable_but_auto_never_starts_on_it(self, mode):
        build = AiService.for_live if mode == "live" else AiService.for_test
        configs = [_config("manual-only", modes=(mode, "no-auto")), _config("normal", modes=(mode,))]

        service = build(configs)
        info = service.get_models_info()

        assert _models(service) == ["manual-only", "normal"]
        assert info["auto"] is True
        assert info["models"][info["current_index"]]["model"] == "normal"

        service.select_model(0)
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
