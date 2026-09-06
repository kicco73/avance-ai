from __future__ import annotations

import pytest

from config import AppConfig, ConfigError

pytestmark = pytest.mark.contract

MINIMAL_CONFIG = """
database:
  url: "sqlite:///:memory:"

chat-service: {}

ai-service:
  providers:
    - driver: gemini
      model: gemini-flash-lite-latest
      key: fake-key

auth-service:
  providers:
    - driver: google
      key: fake-client-id

notification-service:
  url: "smtp://smtp.example.com:587"
  username: fake-username
  password: fake-password
"""


def _write_config(tmp_path, content: str):
    path = tmp_path / ".config.yml"
    path.write_text(content)
    return path


def _load(monkeypatch, tmp_path, content: str) -> AppConfig:
    path = _write_config(tmp_path, content)
    monkeypatch.setattr(AppConfig, "CONFIG_PATHS", [path])
    return AppConfig()


class TestGetOptionalPositiveFloat:
    def _get(self, raw):
        return AppConfig._get_optional_positive_float(raw, "chat-service", "max-session-duration-in-minutes", "cfg", default=60.0)

    def test_returns_the_default_when_absent_and_the_configured_int_or_float_when_present(self):
        assert self._get({"chat-service": {"transport": "rest"}}) == 60.0
        assert self._get({"chat-service": {"max-session-duration-in-minutes": 30}}) == 30.0
        assert self._get({"chat-service": {"max-session-duration-in-minutes": 12.5}}) == 12.5

    @pytest.mark.parametrize("raw", [
        {"chat-service": {"max-session-duration-in-minutes": 0}},
        {"chat-service": {"max-session-duration-in-minutes": -5}},
        {"chat-service": {"max-session-duration-in-minutes": "60"}},
        {"chat-service": {"max-session-duration-in-minutes": True}},
        {"chat-service": {"max-session-duration-in-minutes": None}},
        {},
    ])
    def test_rejects_non_positive_non_numeric_values_and_a_missing_section(self, raw):
        with pytest.raises(ConfigError):
            self._get(raw)


class TestGetOptionalChoice:
    def _get(self, raw):
        return AppConfig._get_optional_choice(raw, "database", "migration-strategy", "cfg", default="stop", choices=("stop", "upgrade", "drop"))

    def test_returns_the_default_when_absent_and_any_listed_choice_when_present(self):
        assert self._get({"database": {"url": "sqlite:///x.db"}}) == "stop"
        for configured in ("stop", "upgrade", "drop"):
            assert self._get({"database": {"migration-strategy": configured}}) == configured

    @pytest.mark.parametrize("bad_value", ["wipe", True, 1, None])
    def test_rejects_values_outside_the_choices(self, bad_value):
        with pytest.raises(ConfigError):
            self._get({"database": {"migration-strategy": bad_value}})


class TestGetOptionalPositiveInt:
    def _get(self, raw):
        return AppConfig._get_optional_positive_int(raw, "jobs", "max_concurrent", "cfg", default=2)

    def test_returns_the_default_when_the_field_or_section_is_absent_and_the_configured_value_when_present(self):
        assert self._get({"jobs": {}}) == 2
        assert self._get({}) == 2
        assert self._get({"jobs": {"max_concurrent": 5}}) == 5

    @pytest.mark.parametrize("raw", [
        {"jobs": {"max_concurrent": 0}},
        {"jobs": {"max_concurrent": -1}},
        {"jobs": {"max_concurrent": 1.5}},
        {"jobs": {"max_concurrent": "2"}},
        {"jobs": {"max_concurrent": True}},
        {"jobs": {"max_concurrent": None}},
        {"jobs": "nope"},
    ])
    def test_rejects_non_positive_non_integer_values_and_a_non_mapping_section(self, raw):
        with pytest.raises(ConfigError):
            self._get(raw)


class TestGetOptionalNonNegativeInt:
    def _get(self, raw, default=0):
        return AppConfig._get_optional_non_negative_int(raw, "jobs", "min_job_interval_ms", "cfg", default=default)

    def test_returns_the_default_when_absent_and_accepts_zero_or_positive_values(self):
        assert self._get({"jobs": {}}) == 0
        assert self._get({"jobs": {"min_job_interval_ms": 500}}) == 500
        assert self._get({"jobs": {"min_job_interval_ms": 0}}, default=1) == 0

    @pytest.mark.parametrize("raw", [
        {"jobs": {"min_job_interval_ms": -1}},
        {"jobs": {"min_job_interval_ms": 1.5}},
        {"jobs": {"min_job_interval_ms": "2"}},
        {"jobs": {"min_job_interval_ms": True}},
        {"jobs": {"min_job_interval_ms": None}},
        {"jobs": "nope"},
    ])
    def test_rejects_negative_non_integer_values_and_a_non_mapping_section(self, raw):
        with pytest.raises(ConfigError):
            self._get(raw)


def _chat(field: str, value) -> str:
    return MINIMAL_CONFIG.replace("chat-service: {}", f"chat-service:\n  {field}: {value}")


def _database(field: str, value) -> str:
    return MINIMAL_CONFIG.replace(
        'database:\n  url: "sqlite:///:memory:"', f'database:\n  url: "sqlite:///:memory:"\n  {field}: {value}'
    )


def _auth(field: str, value) -> str:
    return MINIMAL_CONFIG.replace("auth-service:\n  providers:", f"auth-service:\n  {field}: {value}\n  providers:")


def _section(section: str, field: str, value) -> str:
    return MINIMAL_CONFIG + f"\n{section}:\n  {field}: {value}\n"


SETTINGS = [
    ("max_session_duration_in_minutes", 60.0, _chat("max-session-duration-in-minutes", 15), 15.0, _chat("max-session-duration-in-minutes", 0)),
    ("input_token_budget_per_turn", 16000, _chat("input-token-budget-per-turn", 4000), 4000, _chat("input-token-budget-per-turn", 0)),
    ("total_token_budget_per_session", 200000, _chat("total-token-budget-per-session", 50000), 50000, _chat("total-token-budget-per-session", 0)),
    ("database_migration_strategy", "stop", _database("migration-strategy", "upgrade"), "upgrade", _database("migration-strategy", "wipe")),
    ("auth_token_ttl_in_hours", 24 * 7, _auth("token-ttl-in-hours", 12), 12, _auth("token-ttl-in-hours", 0)),
    ("jobs_shared_max_concurrent", 2, _section("jobs", "shared-max-concurrent", 3), 3, _section("jobs", "shared-max-concurrent", 0)),
    ("test_service_max_concurrent_tests", 4, _section("test-service", "max-concurrent-tests", 5), 5, _section("test-service", "max-concurrent-tests", 0)),
    ("test_service_max_tests_per_minute", 1_000_000, _section("test-service", "max-tests-per-minute", 30), 30, _section("test-service", "max-tests-per-minute", 0)),
    ("test_service_min_test_interval_ms", 0, _section("test-service", "min-test-interval-ms", 500), 500, _section("test-service", "min-test-interval-ms", -1)),
    ("invite_valid_days", 7, _section("project-service", "invite-valid-days", 14), 14, _section("project-service", "invite-valid-days", 0)),
    ("invite_max_shares", 3, _section("project-service", "invite-max-shares", 10), 10, _section("project-service", "invite-max-shares", 0)),
]


class TestOptionalSettingsEndToEnd:
    def test_every_optional_setting_falls_back_to_its_default_when_omitted(self, monkeypatch, tmp_path):
        config = _load(monkeypatch, tmp_path, MINIMAL_CONFIG)
        for attribute, default, _, _, _ in SETTINGS:
            assert getattr(config, attribute) == default, attribute

    @pytest.mark.parametrize(("attribute", "default", "custom_yaml", "custom_value", "invalid_yaml"), SETTINGS, ids=[s[0] for s in SETTINGS])
    def test_reads_a_custom_value_and_rejects_an_invalid_one(self, monkeypatch, tmp_path, attribute, default, custom_yaml, custom_value, invalid_yaml):
        assert getattr(_load(monkeypatch, tmp_path, custom_yaml), attribute) == custom_value
        with pytest.raises(ConfigError):
            _load(monkeypatch, tmp_path, invalid_yaml)

    def test_min_test_interval_accepts_an_explicit_zero(self, monkeypatch, tmp_path):
        config = _load(monkeypatch, tmp_path, _section("test-service", "min-test-interval-ms", 0))
        assert config.test_service_min_test_interval_ms == 0


_ONE_PROVIDER = "ai-service:\n  providers:\n    - driver: gemini\n      model: gemini-flash-lite-latest\n      key: fake-key\n"


def _sole_provider_modes(modes: str) -> str:
    return MINIMAL_CONFIG.replace("      key: fake-key\n", f"      key: fake-key\n      modes: {modes}\n")


def _first_provider_modes_with_sibling(modes: str, sibling: str = "    - driver: gemini\n      model: other-model\n      key: fake-key\n") -> str:
    return MINIMAL_CONFIG.replace(_ONE_PROVIDER, _ONE_PROVIDER + f"      modes: {modes}\n" + sibling)


class TestAiServiceProvidersModes:
    def test_defaults_to_both_reads_an_explicit_both_and_deduplicates_repeats(self, monkeypatch, tmp_path):
        assert _load(monkeypatch, tmp_path, MINIMAL_CONFIG).ai_services[0].modes == ("live", "test")
        assert _load(monkeypatch, tmp_path, _sole_provider_modes("[live, test]")).ai_services[0].modes == ("live", "test")
        assert _load(monkeypatch, tmp_path, _first_provider_modes_with_sibling("[live, live]")).ai_services[0].modes == ("live",)

    def test_a_partial_or_empty_or_no_auto_entry_is_valid_while_a_sibling_covers_the_rest(self, monkeypatch, tmp_path):
        split = _load(monkeypatch, tmp_path, _first_provider_modes_with_sibling(
            "[live]", "    - driver: gemini\n      model: gemini-flash-lite-latest\n      key: fake-key\n      modes: [test]\n"
        ))
        assert split.ai_services[0].modes == ("live",)
        assert split.ai_services[1].modes == ("test",)

        empty = _load(monkeypatch, tmp_path, _first_provider_modes_with_sibling("[]"))
        assert empty.ai_services[0].modes == ()
        assert empty.ai_services[1].modes == ("live", "test")

        assert _load(monkeypatch, tmp_path, _first_provider_modes_with_sibling("[no-auto]")).ai_services[0].modes == ("no-auto",)

        live_no_auto = _load(monkeypatch, tmp_path, _first_provider_modes_with_sibling("[live, no-auto]"))
        assert live_no_auto.ai_services[0].modes == ("live", "no-auto")
        assert live_no_auto.ai_services[1].modes == ("live", "test")

    @pytest.mark.parametrize(("modes", "match"), [
        ("live", None),
        ("[live, staging]", "staging"),
        ("[test]", "'live'"),
        ("[live]", "'test'"),
        ("[live, test, no-auto]", "'live'"),
    ])
    def test_rejects_a_non_list_an_unknown_mode_and_leaving_either_auto_cascade_empty(self, monkeypatch, tmp_path, modes, match):
        with pytest.raises(ConfigError, match=match):
            _load(monkeypatch, tmp_path, _sole_provider_modes(modes))


_WHATSAPP_SERVICE_MINIMAL = """
whatsapp-service:
  enabled: true
  verify-token: my-verify-token
  app-secret: my-app-secret
  access-token: my-access-token
  phone-number-id: "123456"
"""

_WHATSAPP_SERVICE_WITH_PHONE_NUMBER = _WHATSAPP_SERVICE_MINIMAL + "  phone-number: \"+34600000001\"\n"


class TestWhatsAppServiceConfig:
    def test_an_absent_section_or_enabled_false_leaves_the_channel_disabled(self, monkeypatch, tmp_path):
        assert _load(monkeypatch, tmp_path, MINIMAL_CONFIG).whatsapp_service_config is None
        assert _load(monkeypatch, tmp_path, MINIMAL_CONFIG + "\nwhatsapp-service:\n  enabled: false\n").whatsapp_service_config is None

    def test_enabled_parses_with_or_without_a_phone_number_normalized_to_digits_and_a_default_graph_version(self, monkeypatch, tmp_path):
        without_phone = _load(monkeypatch, tmp_path, MINIMAL_CONFIG + "\n" + _WHATSAPP_SERVICE_MINIMAL).whatsapp_service_config
        assert without_phone is not None
        assert without_phone.phone_number is None
        assert without_phone.graph_version == "v23.0"

        with_phone = _load(monkeypatch, tmp_path, MINIMAL_CONFIG + "\n" + _WHATSAPP_SERVICE_WITH_PHONE_NUMBER).whatsapp_service_config
        assert with_phone.phone_number == "34600000001"

    @pytest.mark.parametrize(("content", "match"), [
        (MINIMAL_CONFIG + "\n" + _WHATSAPP_SERVICE_MINIMAL + "  phone-number: not-a-number\n", "phone-number"),
        (MINIMAL_CONFIG + "\nwhatsapp-service:\n  enabled: true\n  app-secret: my-app-secret\n  access-token: my-access-token\n  phone-number-id: \"123456\"\n", "verify-token"),
    ])
    def test_rejects_a_non_digit_phone_number_and_a_missing_verify_token(self, monkeypatch, tmp_path, content, match):
        with pytest.raises(ConfigError, match=match):
            _load(monkeypatch, tmp_path, content)
