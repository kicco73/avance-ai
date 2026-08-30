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
    """Unit tests against the parsing helper directly — no file I/O."""

    def test_returns_the_default_when_the_field_is_absent(self):
        raw = {"chat-service": {"transport": "rest"}}
        value = AppConfig._get_optional_positive_float(raw, "chat-service", "max_session_duration_in_minutes", "cfg", default=60.0)
        assert value == 60.0

    def test_returns_the_configured_value_when_present(self):
        raw = {"chat-service": {"max_session_duration_in_minutes": 30}}
        value = AppConfig._get_optional_positive_float(raw, "chat-service", "max_session_duration_in_minutes", "cfg", default=60.0)
        assert value == 30.0

    def test_accepts_a_float_value(self):
        raw = {"chat-service": {"max_session_duration_in_minutes": 12.5}}
        value = AppConfig._get_optional_positive_float(raw, "chat-service", "max_session_duration_in_minutes", "cfg", default=60.0)
        assert value == 12.5

    @pytest.mark.parametrize("bad_value", [0, -5, "60", True, None])
    def test_rejects_non_positive_or_non_numeric_values(self, bad_value):
        raw = {"chat-service": {"max_session_duration_in_minutes": bad_value}}
        with pytest.raises(ConfigError):
            AppConfig._get_optional_positive_float(raw, "chat-service", "max_session_duration_in_minutes", "cfg", default=60.0)

    def test_raises_if_the_section_itself_is_missing(self):
        with pytest.raises(ConfigError):
            AppConfig._get_optional_positive_float({}, "chat-service", "max_session_duration_in_minutes", "cfg", default=60.0)


class TestMaxSessionDurationInMinutes:
    """End-to-end: a real AppConfig() built from a real (temp) config file."""

    def test_defaults_to_60_when_omitted(self, monkeypatch, tmp_path):
        config = _load(monkeypatch, tmp_path, MINIMAL_CONFIG)
        assert config.max_session_duration_in_minutes == 60.0

    def test_reads_a_custom_value(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG.replace(
            "chat-service: {}",
            "chat-service:\n  max_session_duration_in_minutes: 15",
        )
        config = _load(monkeypatch, tmp_path, content)
        assert config.max_session_duration_in_minutes == 15.0

    def test_rejects_a_non_positive_value(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG.replace(
            "chat-service: {}",
            "chat-service:\n  max_session_duration_in_minutes: 0",
        )
        with pytest.raises(ConfigError):
            _load(monkeypatch, tmp_path, content)


class TestInputTokenBudgetPerSession:
    """End-to-end: a real AppConfig() built from a real (temp) config file."""

    def test_defaults_to_16000_when_omitted(self, monkeypatch, tmp_path):
        config = _load(monkeypatch, tmp_path, MINIMAL_CONFIG)
        assert config.input_token_budget_per_session == 16000

    def test_reads_a_custom_value(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG.replace(
            "chat-service: {}",
            "chat-service:\n  input-token-budget-per-session: 4000",
        )
        config = _load(monkeypatch, tmp_path, content)
        assert config.input_token_budget_per_session == 4000

    def test_rejects_a_non_positive_value(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG.replace(
            "chat-service: {}",
            "chat-service:\n  input-token-budget-per-session: 0",
        )
        with pytest.raises(ConfigError):
            _load(monkeypatch, tmp_path, content)


class TestGetOptionalBool:
    """Unit tests against the parsing helper directly — no file I/O."""

    def test_returns_the_default_when_the_field_is_absent(self):
        raw = {"database": {"url": "sqlite:///x.db"}}
        value = AppConfig._get_optional_bool(raw, "database", "force-drop-and-create-when-incompatible", "cfg", default=False)
        assert value is False

    def test_returns_the_configured_value_when_present(self):
        raw = {"database": {"force-drop-and-create-when-incompatible": True}}
        value = AppConfig._get_optional_bool(raw, "database", "force-drop-and-create-when-incompatible", "cfg", default=False)
        assert value is True

    @pytest.mark.parametrize("bad_value", ["true", 1, 0, None])
    def test_rejects_non_boolean_values(self, bad_value):
        raw = {"database": {"force-drop-and-create-when-incompatible": bad_value}}
        with pytest.raises(ConfigError):
            AppConfig._get_optional_bool(raw, "database", "force-drop-and-create-when-incompatible", "cfg", default=False)


class TestDatabaseForceDropAndCreateWhenIncompatible:
    """End-to-end: a real AppConfig() built from a real (temp) config file."""

    def test_defaults_to_false_when_omitted(self, monkeypatch, tmp_path):
        config = _load(monkeypatch, tmp_path, MINIMAL_CONFIG)
        assert config.database_force_drop_and_create_when_incompatible is False

    def test_reads_a_custom_value(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG.replace(
            'database:\n  url: "sqlite:///:memory:"',
            'database:\n  url: "sqlite:///:memory:"\n  force-drop-and-create-when-incompatible: true',
        )
        config = _load(monkeypatch, tmp_path, content)
        assert config.database_force_drop_and_create_when_incompatible is True

    def test_rejects_a_non_boolean_value(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG.replace(
            'database:\n  url: "sqlite:///:memory:"',
            'database:\n  url: "sqlite:///:memory:"\n  force-drop-and-create-when-incompatible: "yes"',
        )
        with pytest.raises(ConfigError):
            _load(monkeypatch, tmp_path, content)


class TestAuthTokenTtlInHours:
    """End-to-end: a real AppConfig() built from a real (temp) config file."""

    def test_defaults_to_7_days_when_omitted(self, monkeypatch, tmp_path):
        config = _load(monkeypatch, tmp_path, MINIMAL_CONFIG)
        assert config.auth_token_ttl_in_hours == 24 * 7

    def test_reads_a_custom_value(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG.replace(
            "auth-service:\n  providers:",
            "auth-service:\n  token-ttl-in-hours: 12\n  providers:",
        )
        config = _load(monkeypatch, tmp_path, content)
        assert config.auth_token_ttl_in_hours == 12

    def test_rejects_a_non_positive_value(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG.replace(
            "auth-service:\n  providers:",
            "auth-service:\n  token-ttl-in-hours: 0\n  providers:",
        )
        with pytest.raises(ConfigError):
            _load(monkeypatch, tmp_path, content)


class TestGetOptionalPositiveInt:
    """Unit tests against the parsing helper directly — no file I/O."""

    def test_returns_the_default_when_the_field_is_absent(self):
        raw = {"jobs": {}}
        value = AppConfig._get_optional_positive_int(raw, "jobs", "max_concurrent", "cfg", default=2)
        assert value == 2

    def test_returns_the_default_when_the_whole_section_is_absent(self):
        value = AppConfig._get_optional_positive_int({}, "jobs", "max_concurrent", "cfg", default=2)
        assert value == 2

    def test_returns_the_configured_value_when_present(self):
        raw = {"jobs": {"max_concurrent": 5}}
        value = AppConfig._get_optional_positive_int(raw, "jobs", "max_concurrent", "cfg", default=2)
        assert value == 5

    @pytest.mark.parametrize("bad_value", [0, -1, 1.5, "2", True, None])
    def test_rejects_non_positive_or_non_integer_values(self, bad_value):
        raw = {"jobs": {"max_concurrent": bad_value}}
        with pytest.raises(ConfigError):
            AppConfig._get_optional_positive_int(raw, "jobs", "max_concurrent", "cfg", default=2)

    def test_rejects_a_non_mapping_section(self):
        with pytest.raises(ConfigError):
            AppConfig._get_optional_positive_int({"jobs": "nope"}, "jobs", "max_concurrent", "cfg", default=2)


class TestGetOptionalNonNegativeInt:
    """Unit tests against the parsing helper directly — no file I/O."""

    def test_returns_the_default_when_the_field_is_absent(self):
        raw = {"jobs": {}}
        value = AppConfig._get_optional_non_negative_int(raw, "jobs", "min_job_interval_ms", "cfg", default=0)
        assert value == 0

    def test_returns_the_configured_value_when_present(self):
        raw = {"jobs": {"min_job_interval_ms": 500}}
        value = AppConfig._get_optional_non_negative_int(raw, "jobs", "min_job_interval_ms", "cfg", default=0)
        assert value == 500

    def test_accepts_zero(self):
        raw = {"jobs": {"min_job_interval_ms": 0}}
        value = AppConfig._get_optional_non_negative_int(raw, "jobs", "min_job_interval_ms", "cfg", default=1)
        assert value == 0

    @pytest.mark.parametrize("bad_value", [-1, 1.5, "2", True, None])
    def test_rejects_negative_or_non_integer_values(self, bad_value):
        raw = {"jobs": {"min_job_interval_ms": bad_value}}
        with pytest.raises(ConfigError):
            AppConfig._get_optional_non_negative_int(raw, "jobs", "min_job_interval_ms", "cfg", default=0)

    def test_rejects_a_non_mapping_section(self):
        with pytest.raises(ConfigError):
            AppConfig._get_optional_non_negative_int({"jobs": "nope"}, "jobs", "min_job_interval_ms", "cfg", default=0)


class TestJobsSharedMaxConcurrent:
    def test_defaults_when_the_jobs_section_is_omitted(self, monkeypatch, tmp_path):
        config = _load(monkeypatch, tmp_path, MINIMAL_CONFIG)
        assert config.jobs_shared_max_concurrent == 2

    def test_reads_a_custom_value(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG + "\njobs:\n  shared_max_concurrent: 3\n"
        config = _load(monkeypatch, tmp_path, content)
        assert config.jobs_shared_max_concurrent == 3

    def test_rejects_a_non_positive_value(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG + "\njobs:\n  shared_max_concurrent: 0\n"
        with pytest.raises(ConfigError):
            _load(monkeypatch, tmp_path, content)


class TestServiceMaxConcurrentTests:
    def test_defaults_when_the_section_is_omitted(self, monkeypatch, tmp_path):
        config = _load(monkeypatch, tmp_path, MINIMAL_CONFIG)
        assert config.test_service_max_concurrent_tests == 4

    def test_reads_a_custom_value(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG + "\ntest-service:\n  max_concurrent_tests: 5\n"
        config = _load(monkeypatch, tmp_path, content)
        assert config.test_service_max_concurrent_tests == 5

    def test_rejects_a_non_positive_value(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG + "\ntest-service:\n  max_concurrent_tests: 0\n"
        with pytest.raises(ConfigError):
            _load(monkeypatch, tmp_path, content)


class TestServiceMaxTestsPerMinute:
    def test_defaults_when_the_section_is_omitted(self, monkeypatch, tmp_path):
        config = _load(monkeypatch, tmp_path, MINIMAL_CONFIG)
        assert config.test_service_max_tests_per_minute == 1_000_000

    def test_reads_a_custom_value(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG + "\ntest-service:\n  max_tests_per_minute: 30\n"
        config = _load(monkeypatch, tmp_path, content)
        assert config.test_service_max_tests_per_minute == 30

    def test_rejects_a_non_positive_value(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG + "\ntest-service:\n  max_tests_per_minute: 0\n"
        with pytest.raises(ConfigError):
            _load(monkeypatch, tmp_path, content)


class TestServiceMinTestIntervalMs:
    def test_defaults_when_the_section_is_omitted(self, monkeypatch, tmp_path):
        config = _load(monkeypatch, tmp_path, MINIMAL_CONFIG)
        assert config.test_service_min_test_interval_ms == 0

    def test_reads_a_custom_value(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG + "\ntest-service:\n  min_test_interval_ms: 500\n"
        config = _load(monkeypatch, tmp_path, content)
        assert config.test_service_min_test_interval_ms == 500

    def test_accepts_zero(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG + "\ntest-service:\n  min_test_interval_ms: 0\n"
        config = _load(monkeypatch, tmp_path, content)
        assert config.test_service_min_test_interval_ms == 0

    def test_rejects_a_negative_value(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG + "\ntest-service:\n  min_test_interval_ms: -1\n"
        with pytest.raises(ConfigError):
            _load(monkeypatch, tmp_path, content)
