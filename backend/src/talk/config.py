from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import optional_providers, optional_str, parse_ui_fields, provider_prefix, require_str

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
        prefix = provider_prefix(entry, SECTION, i, path)
        driver = require_str(entry, prefix, "driver", path)
        model = require_str(entry, prefix, "model", path)
        key = optional_str(entry, prefix, "key", path)
        ui_label, ui_description = parse_ui_fields(entry, driver, SECTION, i, path)
        services.append(TalkServiceConfig(
            driver=driver, model=model, key=key, ui_label=ui_label, ui_description=ui_description,
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
