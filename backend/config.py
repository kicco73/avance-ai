"""Typed access to backend/.config.yml, the single source of application
configuration (replaces the old .env/os.environ approach). Read and
validated once at startup by main.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / ".config.yml"

_VALID_TRANSPORTS = ("websocket", "rest")


class ConfigError(Exception):
    """Raised when backend/.config.yml is missing or structurally invalid."""


@dataclass(frozen=True)
class AiServiceConfig:
    name: str
    model: str
    key: str


class AppConfig:
    def __init__(self, path: Path = DEFAULT_CONFIG_PATH) -> None:
        if not path.is_file():
            raise ConfigError(
                f"Configuration file not found: {path}. Copy .config.example.yml "
                f"to {path.name} and fill in your values."
            )
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if not isinstance(raw, dict):
            raise ConfigError(f"{path} must contain a YAML mapping at the top level.")

        self.database_url = _require_str(raw, "database", "url", path)

        self.chat_transport = _require_str(raw, "chat-service", "transport", path)
        if self.chat_transport not in _VALID_TRANSPORTS:
            raise ConfigError(
                f"{path}: chat-service.transport={self.chat_transport!r} is not "
                f"valid. Allowed values: {', '.join(_VALID_TRANSPORTS)}."
            )

        self.ai_services = _parse_ai_services(raw, path)

        model_service = raw.get("model-service")
        if not isinstance(model_service, dict):
            raise ConfigError(f"{path}: 'model-service' section is missing or not a mapping.")
        file_watch_enabled = model_service.get("file-watch-enabled")
        if not isinstance(file_watch_enabled, bool):
            raise ConfigError(f"{path}: 'model-service.file-watch-enabled' must be a boolean.")
        self.model_file_watch_enabled = file_watch_enabled


def _require_str(raw: dict, section: str, field: str, path: Path) -> str:
    sub = raw.get(section)
    if not isinstance(sub, dict):
        raise ConfigError(f"{path}: '{section}' section is missing or not a mapping.")
    value = sub.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}: '{section}.{field}' is missing or empty.")
    return value.strip()


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
