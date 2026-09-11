from __future__ import annotations

from system import bus
from system.bus import POINT_CONFIG_SERVICES


def talk_configured() -> bool:
    return bool(bus.collect(POINT_CONFIG_SERVICES, {}).get("talk", {}).get("enabled"))


def ui_section(ui_label: str, ui_description: str, fields: dict) -> dict:
    return {**fields, "ui-label": ui_label, "ui-description": ui_description}
