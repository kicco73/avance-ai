"""Typed access to backend/.config.yml, the single source of application
configuration (replaces the old .env/os.environ approach). Read and
validated once at startup by main.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import yaml

class ConfigError(Exception):
    """Raised when backend/.config.yml is missing or structurally invalid."""

@dataclass(frozen=True)
class AiServiceConfig:
    name: str
    model: str
    key: str


@dataclass(frozen=True)
class TalkServiceConfig:
    name: str
    model: str
    # Optional: a future local provider (e.g. Piper) won't need one.
    key: str | None


@dataclass(frozen=True)
class ListenServiceConfig:
    name: str
    model: str
    # Optional: unused by faster-whisper, kept for a future remote provider.
    key: str | None
    # Optional: skips faster-whisper's autodetect when given (e.g. "ca").
    language: str | None


class AppConfig:

    VALID_TRANSPORTS = ("websocket", "rest")
    CONFIG_PATHS = [
        Path(__file__).resolve().parent / ".config.yml",
        Path('/etc/secrets') / "avance.yml",
    ]

    @classmethod
    def _load_yml(cls) -> dict:
        for path in cls.CONFIG_PATHS:
            if not path.is_file():
                continue
            with path.open("r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
                return raw, path
        return None, None
    
    @staticmethod
    def _require_str(raw: dict, section: str, field: str, path: Path) -> str:
        sub = raw.get(section)
        if not isinstance(sub, dict):
            raise ConfigError(f"{path}: '{section}' section is missing or not a mapping.")
        value = sub.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{path}: '{section}.{field}' is missing or empty.")
        return value.strip()

    @staticmethod
    def _get_section(raw: dict, section: str, path: Path) -> dict:
        sub = raw.get(section)
        if not isinstance(sub, dict):
            raise ConfigError(f"{path}: '{section}' section is missing or not a mapping.")
        return sub

    @classmethod
    def _get_providers(cls, raw: dict, section: str, path: Path) -> list:
        sub = cls._get_section(raw, section, path)
        entries = sub.get("providers")
        if not isinstance(entries, list) or not entries:
            raise ConfigError(f"{path}: '{section}.providers' must be a non-empty list.")
        return entries

    @classmethod
    def _get_optional_providers(cls, raw: dict, section: str, path: Path) -> list | None:
        """None if the whole section is absent, or `section.enabled` is
        absent/false — the caller skips the service entirely, off by
        default. Otherwise the same non-empty providers list as
        _get_providers."""
        sub = raw.get(section)
        if sub is None:
            return None
        if not isinstance(sub, dict):
            raise ConfigError(f"{path}: '{section}' section is not a mapping.")
        enabled = sub.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ConfigError(f"{path}: '{section}.enabled' must be a boolean.")
        if not enabled:
            return None
        entries = sub.get("providers")
        if not isinstance(entries, list) or not entries:
            raise ConfigError(f"{path}: '{section}.providers' must be a non-empty list.")
        return entries

    @classmethod
    def _parse_talk_services(cls, raw: dict, path: Path) -> list[TalkServiceConfig] | None:
        entries = cls._get_optional_providers(raw, "talk-service", path)
        if entries is None:
            return None

        services = []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ConfigError(f"{path}: 'talk-service.providers[{i}]' must be a mapping.")
            name = entry.get("name")
            model = entry.get("model")
            key = entry.get("key")
            if not isinstance(name, str) or not name.strip():
                raise ConfigError(f"{path}: 'talk-service.providers[{i}].name' is missing or empty.")
            if not isinstance(model, str) or not model.strip():
                raise ConfigError(f"{path}: 'talk-service.providers[{i}].model' is missing or empty.")
            if key is not None and not isinstance(key, str):
                raise ConfigError(f"{path}: 'talk-service.providers[{i}].key' must be a string if present.")
            services.append(TalkServiceConfig(name=name.strip(), model=model.strip(), key=key))
        return services

    @classmethod
    def _parse_listen_services(cls, raw: dict, path: Path) -> list[ListenServiceConfig] | None:
        entries = cls._get_optional_providers(raw, "listen-service", path)
        if entries is None:
            return None

        services = []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ConfigError(f"{path}: 'listen-service.providers[{i}]' must be a mapping.")
            name = entry.get("name")
            model = entry.get("model")
            key = entry.get("key")
            language = entry.get("language")
            if not isinstance(name, str) or not name.strip():
                raise ConfigError(f"{path}: 'listen-service.providers[{i}].name' is missing or empty.")
            if not isinstance(model, str) or not model.strip():
                raise ConfigError(f"{path}: 'listen-service.providers[{i}].model' is missing or empty.")
            if key is not None and not isinstance(key, str):
                raise ConfigError(f"{path}: 'listen-service.providers[{i}].key' must be a string if present.")
            if language is not None and not isinstance(language, str):
                raise ConfigError(f"{path}: 'listen-service.providers[{i}].language' must be a string if present.")
            services.append(ListenServiceConfig(
                name=name.strip(), model=model.strip(), key=key,
                language=language.strip() if language else None,
            ))
        return services

    @classmethod
    def _parse_ai_services(cls, raw: dict, path: Path) -> list[AiServiceConfig]:
        entries = cls._get_providers(raw, "ai-service", path)

        services = []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ConfigError(f"{path}: 'ai-service.providers[{i}]' must be a mapping.")
            name = entry.get("name")
            model = entry.get("model")
            key = entry.get("key")
            if not isinstance(name, str) or not name.strip():
                raise ConfigError(f"{path}: 'ai-service.providers[{i}].name' is missing or empty.")
            if not isinstance(model, str) or not model.strip():
                raise ConfigError(f"{path}: 'ai-service.providers[{i}].model' is missing or empty.")
            if not isinstance(key, str):
                raise ConfigError(f"{path}: 'ai-service.providers[{i}].key' must be a string.")
            services.append(AiServiceConfig(name=name.strip(), model=model.strip(), key=key))
        return services

    def __init__(self) -> None:

        raw, path = self._load_yml()
        if not isinstance(raw, dict):
            raise ConfigError(f"{path} must contain a YAML mapping at the top level.")

        self.database_url = self._require_str(raw, "database", "url", path)
        self.chat_transport = self._require_str(raw, "chat-service", "transport", path)
        if self.chat_transport not in self.VALID_TRANSPORTS:
            raise ConfigError(
                f"{path}: chat-service.transport={self.chat_transport!r} is not "
                f"valid. Allowed values: {', '.join(self.VALID_TRANSPORTS)}."
            )

        self.ai_services = self._parse_ai_services(raw, path)
        self.talk_services = self._parse_talk_services(raw, path)
        self.listen_services = self._parse_listen_services(raw, path)


