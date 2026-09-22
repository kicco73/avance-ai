from __future__ import annotations

import pytest

from config import (
    DEFAULT_ALLOWED_ORIGINS, AppConfig, ConfigError, ReplyDeadline, optional_choice, optional_non_negative_int,
    optional_positive_int, optional_section,
)

pytestmark = pytest.mark.contract

MINIMAL_CONFIG = """
database:
  url: "sqlite:///:memory:"

turn-service: {}

ai-service:
  providers:
    - driver: gemini
      model: gemini-flash-lite-latest
      key: fake-key

auth-service:
  providers:
    - driver: google
      key: fake-client-id

mail-service:
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


class TestOptionalSection:
    def test_an_absent_section_reads_as_empty_the_present_one_is_returned_and_a_non_mapping_one_is_rejected(self):
        assert optional_section({}, "jobs", "cfg") == {}
        assert optional_section({"jobs": {"max_concurrent": 5}}, "jobs", "cfg") == {"max_concurrent": 5}
        with pytest.raises(ConfigError):
            optional_section({"jobs": "nope"}, "jobs", "cfg")


class TestOptionalChoice:
    def _get(self, section):
        return optional_choice(section, "database", "migration-strategy", "cfg", "stop", ("stop", "upgrade", "drop"))

    def test_returns_the_default_when_absent_and_any_listed_choice_when_present(self):
        assert self._get({"url": "sqlite:///x.db"}) == "stop"
        for configured in ("stop", "upgrade", "drop"):
            assert self._get({"migration-strategy": configured}) == configured

    @pytest.mark.parametrize("bad_value", ["wipe", True, 1, None])
    def test_rejects_values_outside_the_choices(self, bad_value):
        with pytest.raises(ConfigError):
            self._get({"migration-strategy": bad_value})


class TestOptionalPositiveInt:
    def _get(self, section):
        return optional_positive_int(section, "jobs", "max_concurrent", "cfg", 2)

    def test_returns_the_default_when_the_field_is_absent_and_the_configured_value_when_present(self):
        assert self._get({}) == 2
        assert self._get({"max_concurrent": 5}) == 5

    @pytest.mark.parametrize("bad_value", [0, -1, 1.5, "2", True, None])
    def test_rejects_non_positive_and_non_integer_values(self, bad_value):
        with pytest.raises(ConfigError):
            self._get({"max_concurrent": bad_value})


class TestOptionalNonNegativeInt:
    def _get(self, section, default=0):
        return optional_non_negative_int(section, "jobs", "min_job_interval_ms", "cfg", default)

    def test_returns_the_default_when_absent_and_accepts_zero_or_positive_values(self):
        assert self._get({}) == 0
        assert self._get({"min_job_interval_ms": 500}) == 500
        assert self._get({"min_job_interval_ms": 0}, default=1) == 0

    @pytest.mark.parametrize("bad_value", [-1, 1.5, "2", True, None])
    def test_rejects_negative_and_non_integer_values(self, bad_value):
        with pytest.raises(ConfigError):
            self._get({"min_job_interval_ms": bad_value})


def _chat(field: str, value) -> str:
    return MINIMAL_CONFIG.replace("turn-service: {}", f"turn-service:\n  {field}: {value}")


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
    ("jobs_shared_max_concurrent", 2, _section("scheduler-service", "shared-max-concurrent", 3), 3, _section("scheduler-service", "shared-max-concurrent", 0)),
    ("invite_valid_days", 7, _section("project-service", "invite-valid-days", 14), 14, _section("project-service", "invite-valid-days", 0)),
    ("invite_max_shares", 3, _section("project-service", "invite-max-shares", 10), 10, _section("project-service", "invite-max-shares", 0)),
    ("reply_silence_seconds", 45.0, _chat("reply-silence-seconds", 90), 90.0, _chat("reply-silence-seconds", 0)),
]


def _deadlines(**fields) -> str:
    lines = "".join(f"\n  {name.replace('_', '-')}: {value}" for name, value in fields.items())
    return MINIMAL_CONFIG.replace("turn-service: {}", f"turn-service:{lines}")


class TestReplyDeadlines:
    def test_the_model_deadlines_default_to_the_reply_deadline_config_ships_with(self, monkeypatch, tmp_path):
        assert _load(monkeypatch, tmp_path, MINIMAL_CONFIG).stream_deadline == ReplyDeadline()

    def test_each_model_deadline_is_read_from_turn_service(self, monkeypatch, tmp_path):
        config = _load(monkeypatch, tmp_path, _deadlines(first_chunk_seconds=2, next_chunk_seconds=4.5, silent_round_seconds=20))

        assert config.stream_deadline == ReplyDeadline(first_chunk_seconds=2.0, next_chunk_seconds=4.5, silent_round_seconds=20.0)

    @pytest.mark.parametrize("field", ["first-chunk-seconds", "next-chunk-seconds", "silent-round-seconds"])
    def test_a_model_deadline_must_be_a_positive_number(self, monkeypatch, tmp_path, field):
        with pytest.raises(ConfigError):
            _load(monkeypatch, tmp_path, _chat(field, 0))

    def test_the_browser_must_wait_longer_than_the_server_lets_the_model_stay_silent(self, monkeypatch, tmp_path):
        with pytest.raises(ConfigError, match="reply-silence-seconds"):
            _load(monkeypatch, tmp_path, _deadlines(silent_round_seconds=45))
        with pytest.raises(ConfigError, match="reply-silence-seconds"):
            _load(monkeypatch, tmp_path, _deadlines(silent_round_seconds=30, reply_silence_seconds=30))

        config = _load(monkeypatch, tmp_path, _deadlines(silent_round_seconds=60, reply_silence_seconds=61))

        assert config.reply_silence_seconds == 61.0

    def test_manage_services_shows_all_four(self, monkeypatch, tmp_path):
        chat = _load(monkeypatch, tmp_path, MINIMAL_CONFIG).public_services_snapshot()["chat"]

        assert {key: chat[key] for key in (
            "first-chunk-seconds", "next-chunk-seconds", "silent-round-seconds", "reply-silence-seconds",
        )} == {"first-chunk-seconds": 10.0, "next-chunk-seconds": 10.0, "silent-round-seconds": 30.0, "reply-silence-seconds": 45.0}


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


class TestMaxSessionDurationInMinutes:
    def test_reads_an_int_or_a_float_and_always_yields_a_float(self, monkeypatch, tmp_path):
        assert _load(monkeypatch, tmp_path, _chat("max-session-duration-in-minutes", 30)).max_session_duration_in_minutes == 30.0
        assert _load(monkeypatch, tmp_path, _chat("max-session-duration-in-minutes", 12.5)).max_session_duration_in_minutes == 12.5

    @pytest.mark.parametrize("bad_value", ["-5", '"60"', "true", "null"])
    def test_rejects_non_positive_and_non_numeric_values(self, monkeypatch, tmp_path, bad_value):
        with pytest.raises(ConfigError):
            _load(monkeypatch, tmp_path, _chat("max-session-duration-in-minutes", bad_value))

    def test_rejects_a_missing_turn_service_section(self, monkeypatch, tmp_path):
        with pytest.raises(ConfigError):
            _load(monkeypatch, tmp_path, MINIMAL_CONFIG.replace("turn-service: {}", ""))


class TestAllowedOrigins:
    def test_defaults_to_the_frontend_dev_server_when_the_section_is_absent(self, monkeypatch, tmp_path):
        assert _load(monkeypatch, tmp_path, MINIMAL_CONFIG).allowed_origins == list(DEFAULT_ALLOWED_ORIGINS)

    def test_a_configured_list_replaces_the_default_and_loses_its_trailing_slashes(self, monkeypatch, tmp_path):
        config = _load(monkeypatch, tmp_path, MINIMAL_CONFIG + (
            "\nweb:\n  allowed-origins:\n    - https://app.example.com/\n    -  https://admin.example.com \n"
        ))
        assert config.allowed_origins == ["https://app.example.com", "https://admin.example.com"]

    @pytest.mark.parametrize("bad_value", ["https://app.example.com", "{origin: https://app.example.com}", "[1]", '[""]', "true"])
    def test_rejects_anything_that_is_not_a_list_of_non_empty_strings(self, monkeypatch, tmp_path, bad_value):
        with pytest.raises(ConfigError, match="allowed-origins"):
            _load(monkeypatch, tmp_path, MINIMAL_CONFIG + f"\nweb:\n  allowed-origins: {bad_value}\n")

    def test_an_explicitly_empty_list_allows_no_cross_origin_caller_at_all(self, monkeypatch, tmp_path):
        assert _load(monkeypatch, tmp_path, MINIMAL_CONFIG + "\nweb:\n  allowed-origins: []\n").allowed_origins == []
