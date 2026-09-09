from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import ConfigError, optional_providers, parse_ui_fields

SECTION = "talk-service"


@dataclass(frozen=True)
class TalkServiceConfig:
    driver: str
    model: str
    key: str | None
    ui_label: str
    ui_description: str | None = None


def parse(raw: dict, path: Path) -> list[TalkServiceConfig]:
    """The configured providers, empty when the section is absent or not
    enabled — the one shape a caller can act on without asking whether
    there is anything there."""
    entries = optional_providers(raw, SECTION, path) or []
    services = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: '{SECTION}.providers[{i}]' must be a mapping.")
        driver = entry.get("driver")
        model = entry.get("model")
        key = entry.get("key")
        if not isinstance(driver, str) or not driver.strip():
            raise ConfigError(f"{path}: '{SECTION}.providers[{i}].driver' is missing or empty.")
        if not isinstance(model, str) or not model.strip():
            raise ConfigError(f"{path}: '{SECTION}.providers[{i}].model' is missing or empty.")
        if key is not None and not isinstance(key, str):
            raise ConfigError(f"{path}: '{SECTION}.providers[{i}].key' must be a string if present.")
        driver = driver.strip()
        ui_label, ui_description = parse_ui_fields(entry, driver, SECTION, i, path)
        services.append(TalkServiceConfig(
            driver=driver, model=model.strip(), key=key, ui_label=ui_label, ui_description=ui_description,
        ))
    return services


def public_fields(services: list[TalkServiceConfig]) -> dict:
    return {
        "enabled": bool(services),
        "providers": [
            {"driver": p.driver, "model": p.model, "ui-label": p.ui_label, "ui-description": p.ui_description}
            for p in services
        ],
    }
