"""Listen's own `listen-service` section.

The core never parses this. `AppConfig` hands out the configuration file
already read (`AppConfig.raw`), and each service reads the part that
belongs to it — so a build without Listen has no dangling parser for a
section it will never see, and adding a driver never touches config.py.

The price, stated because it is real: an error in this section surfaces
when Listen starts rather than when the file is read. Everything else in
`.config.yml` is still validated up front.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import optional_providers, optional_str, parse_ui_fields, provider_prefix, require_str

SECTION = "listen-service"

_DRIVERS = ("faster-whisper",)


@dataclass(frozen=True)
class ListenServiceConfig:
    driver: str
    model: str
    # Optional: unused by faster-whisper, kept for a future remote provider.
    key: str | None
    # Optional: skips faster-whisper's autodetect when given (e.g. "ca").
    language: str | None
    # Optional: falls back to `driver`.
    ui_label: str
    ui_description: str | None = None


def parse(raw: dict, path: Path) -> list[ListenServiceConfig] | None:
    """The configured providers, or None when the section is absent or
    not enabled — which is how a deployment turns Listen off without
    removing it."""
    entries = optional_providers(raw, SECTION, path)
    if entries is None:
        return None
    services = []
    for i, entry in enumerate(entries):
        prefix = provider_prefix(entry, SECTION, i, path)
        driver = require_str(entry, prefix, "driver", path)
        model = require_str(entry, prefix, "model", path)
        key = optional_str(entry, prefix, "key", path)
        language = optional_str(entry, prefix, "language", path)
        ui_label, ui_description = parse_ui_fields(entry, driver, SECTION, i, path)
        services.append(ListenServiceConfig(
            driver=driver, model=model, key=key,
            language=language.strip() if language and language.strip() else None,
            ui_label=ui_label, ui_description=ui_description,
        ))
    return services


def public_fields(services: list[ListenServiceConfig] | None) -> dict:
    """What Settings > Manage services shows for this section — the same
    read-only shape every other service contributes."""
    return {
        "enabled": services is not None,
        "providers": [
            {
                "driver": p.driver, "model": p.model,
                "ui-label": p.ui_label, "ui-description": p.ui_description,
                "language": p.language,
            }
            for p in (services or [])
        ],
    }
