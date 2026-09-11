from __future__ import annotations

def ui_section(ui_label: str, ui_description: str, fields: dict) -> dict:
    return {**fields, "ui-label": ui_label, "ui-description": ui_description}
