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
class AudioServiceConfig:
    name: str
    model: str
    # Optional: a future local provider (e.g. Piper) won't need one.
    key: str | None


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
    def _parse_audio_services(raw: dict, path: Path) -> list[AudioServiceConfig]:
        entries = raw.get("audio-service")
        if not isinstance(entries, list) or not entries:
            raise ConfigError(f"{path}: 'audio-service' must be a non-empty list.")

        services = []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ConfigError(f"{path}: 'audio-service[{i}]' must be a mapping.")
            name = entry.get("name")
            model = entry.get("model")
            key = entry.get("key")
            if not isinstance(name, str) or not name.strip():
                raise ConfigError(f"{path}: 'audio-service[{i}].name' is missing or empty.")
            if not isinstance(model, str) or not model.strip():
                raise ConfigError(f"{path}: 'audio-service[{i}].model' is missing or empty.")
            if key is not None and not isinstance(key, str):
                raise ConfigError(f"{path}: 'audio-service[{i}].key' must be a string if present.")
            services.append(AudioServiceConfig(name=name.strip(), model=model.strip(), key=key))
        return services

    @staticmethod
    def _parse_ai_services(raw: dict, path: Path) -> list[AiServiceConfig]:
        entries = raw.get("ai-service")
        if not isinstance(entries, list) or not entries:
            raise ConfigError(f"{path}: 'ai-service' must be a non-empty list.")

        services = []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ConfigError(f"{path}: 'ai-service[{i}]' must be a mapping.")
            name = entry.get("name")
            model = entry.get("model")
            key = entry.get("key")
            if not isinstance(name, str) or not name.strip():
                raise ConfigError(f"{path}: 'ai-service[{i}].name' is missing or empty.")
            if not isinstance(model, str) or not model.strip():
                raise ConfigError(f"{path}: 'ai-service[{i}].model' is missing or empty.")
            if not isinstance(key, str):
                raise ConfigError(f"{path}: 'ai-service[{i}].key' must be a string.")
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
        self.audio_services = self._parse_audio_services(raw, path)

        model_service = raw.get("model-service")
        if not isinstance(model_service, dict):
            raise ConfigError(f"{path}: 'model-service' section is missing or not a mapping.")
        file_watch_enabled = model_service.get("file-watch-enabled")
        if not isinstance(file_watch_enabled, bool):
            raise ConfigError(f"{path}: 'model-service.file-watch-enabled' must be a boolean.")
        self.model_file_watch_enabled = file_watch_enabled


