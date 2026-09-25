from __future__ import annotations

import pytest
from ruamel.yaml import YAML

from ai import config as ai_config
from config import ConfigError

pytestmark = pytest.mark.contract

_ONE_PROVIDER = "ai-service:\n  providers:\n    - driver: gemini\n      model: gemini-flash-lite-latest\n      key: fake-key\n"


def _load(tmp_path, content: str):
    path = tmp_path / ".config.yml"
    path.write_text(content)
    with path.open("r", encoding="utf-8") as f:
        raw = YAML(typ="rt").load(f)
    return ai_config.parse(raw, path)


def _sole_provider_modes(modes: str) -> str:
    return _ONE_PROVIDER.replace("      key: fake-key\n", f"      key: fake-key\n      modes: {modes}\n")


def _first_provider_modes_with_sibling(modes: str, sibling: str = "    - driver: gemini\n      model: other-model\n      key: fake-key\n") -> str:
    return _ONE_PROVIDER.replace(_ONE_PROVIDER, _ONE_PROVIDER + f"      modes: {modes}\n" + sibling)


class TestAiServiceSectionOptionality:
    def test_an_absent_section_parses_to_none(self, tmp_path):
        assert _load(tmp_path, "database:\n  url: sqlite:///x.db\n") is None

    def test_a_present_section_parses_to_a_non_empty_list(self, tmp_path):
        configs = _load(tmp_path, _ONE_PROVIDER)
        assert configs is not None and len(configs) == 1
        assert configs[0].driver == "gemini"


class TestAiServiceProvidersModes:
    def test_defaults_to_both_reads_an_explicit_both_and_deduplicates_repeats(self, tmp_path):
        assert _load(tmp_path, _ONE_PROVIDER)[0].modes == ("live", "test")
        assert _load(tmp_path, _sole_provider_modes("[live, test]"))[0].modes == ("live", "test")
        assert _load(tmp_path, _first_provider_modes_with_sibling("[live, live]"))[0].modes == ("live",)

    def test_a_partial_or_empty_or_no_auto_entry_is_valid_while_a_sibling_covers_the_rest(self, tmp_path):
        split = _load(tmp_path, _first_provider_modes_with_sibling(
            "[live]", "    - driver: gemini\n      model: gemini-flash-lite-latest\n      key: fake-key\n      modes: [test]\n"
        ))
        assert split[0].modes == ("live",)
        assert split[1].modes == ("test",)

        empty = _load(tmp_path, _first_provider_modes_with_sibling("[]"))
        assert empty[0].modes == ()
        assert empty[1].modes == ("live", "test")

        assert _load(tmp_path, _first_provider_modes_with_sibling("[no-auto]"))[0].modes == ("no-auto",)

        live_no_auto = _load(tmp_path, _first_provider_modes_with_sibling("[live, no-auto]"))
        assert live_no_auto[0].modes == ("live", "no-auto")
        assert live_no_auto[1].modes == ("live", "test")

    @pytest.mark.parametrize(("modes", "match"), [
        ("live", None),
        ("[live, staging]", "staging"),
        ("[test]", "'live'"),
        ("[live]", "'test'"),
        ("[live, test, no-auto]", "'live'"),
    ])
    def test_rejects_a_non_list_an_unknown_mode_and_leaving_either_auto_cascade_empty(self, tmp_path, modes, match):
        with pytest.raises(ConfigError, match=match):
            _load(tmp_path, _sole_provider_modes(modes))


class TestAiServiceProviderPrices:
    def _with(self, lines: str) -> str:
        return _ONE_PROVIDER + lines

    def test_prices_default_to_zero_when_absent_or_left_empty(self, tmp_path):
        absent, = _load(tmp_path, _ONE_PROVIDER)
        empty, = _load(tmp_path, self._with("      input-token-ppm:\n      output-token-ppm:\n      thought-token-ppm:\n"))

        for config in (absent, empty):
            assert (config.input_token_ppm, config.output_token_ppm, config.thought_token_ppm) == (0.0, 0.0, 0.0)

    def test_prices_are_read_as_floats_per_million_tokens(self, tmp_path):
        config, = _load(tmp_path, self._with(
            "      input-token-ppm: 0.1\n      output-token-ppm: 0.4\n      thought-token-ppm: 3\n",
        ))

        assert (config.input_token_ppm, config.output_token_ppm, config.thought_token_ppm) == (0.1, 0.4, 3.0)
        assert ai_config.public_fields([config])["providers"][0]["thought-token-ppm"] == 3.0

    @pytest.mark.parametrize("value", ["-1", "cheap", "true"])
    def test_a_negative_or_non_numeric_price_is_refused(self, tmp_path, value):
        with pytest.raises(ConfigError):
            _load(tmp_path, self._with(f"      input-token-ppm: {value}\n"))
