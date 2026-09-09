from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import ConfigError

SECTION = "mail-service"


@dataclass(frozen=True)
class MailServiceConfig:
    url: str
    username: str
    password: str
    from_name: str | None
    timeout_seconds: int


def _require_str(section: dict, field: str, path: Path) -> str:
    value = section.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path}: '{SECTION}.{field}' is missing or empty.")
    return value.strip()


def parse(raw: dict, path: Path) -> MailServiceConfig | None:
    section = raw.get(SECTION)
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ConfigError(f"{path}: '{SECTION}' section is not a mapping.")
    url = _require_str(section, "url", path)
    username = _require_str(section, "username", path)
    password = _require_str(section, "password", path)
    from_name = section.get("from-name")
    if from_name is not None and not isinstance(from_name, str):
        raise ConfigError(f"{path}: '{SECTION}.from-name' must be a string if present.")
    timeout_seconds = section.get("timeout-seconds", 10)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        raise ConfigError(f"{path}: '{SECTION}.timeout-seconds' must be a positive integer if present.")
    return MailServiceConfig(
        url=url, username=username, password=password,
        from_name=from_name.strip() if from_name and from_name.strip() else None,
        timeout_seconds=timeout_seconds,
    )


def public_fields(config: MailServiceConfig | None) -> dict:
    return {"enabled": config is not None}
