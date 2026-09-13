from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import optional_positive_int, optional_section, optional_str, require_str

SECTION = "mail-service"


@dataclass(frozen=True)
class MailServiceConfig:
    url: str
    username: str
    password: str
    from_name: str | None
    timeout_seconds: int


def parse(raw: dict, path: Path) -> MailServiceConfig | None:
    if raw.get(SECTION) is None:
        return None
    section = optional_section(raw, SECTION, path)
    url = require_str(section, SECTION, "url", path)
    username = require_str(section, SECTION, "username", path)
    password = require_str(section, SECTION, "password", path)
    from_name = optional_str(section, SECTION, "from-name", path)
    return MailServiceConfig(
        url=url, username=username, password=password,
        from_name=from_name.strip() if from_name and from_name.strip() else None,
        timeout_seconds=optional_positive_int(section, SECTION, "timeout-seconds", path, 10),
    )


def public_fields(config: MailServiceConfig | None) -> dict:
    return {"enabled": config is not None}
