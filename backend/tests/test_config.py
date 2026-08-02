from __future__ import annotations

import pytest

from config import AppConfig, ConfigError

MINIMAL_CONFIG = """
database:
  url: "sqlite:///:memory:"

chat-service:
  transport: rest

ai-service:
  providers:
    - driver: gemini
      model: gemini-flash-lite-latest
      key: fake-key
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
            "chat-service:\n  transport: rest",
            "chat-service:\n  transport: rest\n  max_session_duration_in_minutes: 15",
        )
        config = _load(monkeypatch, tmp_path, content)
        assert config.max_session_duration_in_minutes == 15.0

    def test_rejects_a_non_positive_value(self, monkeypatch, tmp_path):
        content = MINIMAL_CONFIG.replace(
            "chat-service:\n  transport: rest",
            "chat-service:\n  transport: rest\n  max_session_duration_in_minutes: 0",
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
