from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config import ConfigError, optional_choice, optional_section, optional_str, require_str

SECTION = "whatsapp-service"

_VOICE_REPLIES = ("never", "when-spoken-to", "always")


@dataclass(frozen=True)
class WhatsAppServiceConfig:
    verify_token: str
    app_secret: str
    access_token: str
    phone_number_id: str
    phone_number: str | None
    invite_prefix: str
    graph_version: str
    mark_read: bool
    # When the bot answers with a voice note instead of text (needs
    # talk-service): "never", "when-spoken-to" (only in reply to a voice
    # note — the default), "always" (every reply that has an [audio] text).
    voice_replies: str


def parse(raw: dict, path: Path) -> WhatsAppServiceConfig | None:
    section = optional_section(raw, SECTION, path)
    if not section.get("enabled", False):
        return None
    verify_token = require_str(section, SECTION, "verify-token", path)
    app_secret = require_str(section, SECTION, "app-secret", path)
    access_token = require_str(section, SECTION, "access-token", path)

    # YAML reads an unquoted 1223547060851510 as an int: accept both
    # (going through require_str first would reject the int).
    phone_number_id = section.get("phone-number-id")
    if isinstance(phone_number_id, int) and not isinstance(phone_number_id, bool):
        phone_number_id = str(phone_number_id)
    if not isinstance(phone_number_id, str) or not phone_number_id.strip():
        raise ConfigError(f"{path}: '{SECTION}.phone-number-id' is missing or empty.")
    phone_number_id = phone_number_id.strip()

    phone_number = optional_str(section, SECTION, "phone-number", path)
    if phone_number is not None:
        phone_number = phone_number.strip().lstrip("+")
        if not phone_number.isdigit():
            raise ConfigError(f"{path}: '{SECTION}.phone-number' must be digits only (E.164, no '+').")

    invite_prefix = section.get("invite-prefix", "Invitation code: ")
    if not isinstance(invite_prefix, str):
        raise ConfigError(f"{path}: '{SECTION}.invite-prefix' must be a string if present.")

    graph_version = section.get("graph-version", "v23.0")
    if not isinstance(graph_version, str) or not graph_version.strip():
        raise ConfigError(f"{path}: '{SECTION}.graph-version' must be a non-empty string if present.")

    mark_read = section.get("mark-read", True)
    if not isinstance(mark_read, bool):
        raise ConfigError(f"{path}: '{SECTION}.mark-read' must be a boolean if present.")

    voice_replies = optional_choice(section, SECTION, "voice-replies", path, "when-spoken-to", _VOICE_REPLIES)

    return WhatsAppServiceConfig(
        verify_token=verify_token, app_secret=app_secret, access_token=access_token,
        phone_number_id=phone_number_id, phone_number=phone_number, invite_prefix=invite_prefix,
        graph_version=graph_version.strip(), mark_read=mark_read, voice_replies=voice_replies,
    )


def public_fields(config: WhatsAppServiceConfig | None) -> dict:
    """Same shape AppConfig.public_services_snapshot() used to build for
    this section: its own three secrets included, since Manage services
    shows them as masked/revealable fields on an admin-only route."""
    return {
        "enabled": config is not None,
        "verify-token": config.verify_token if config else None,
        "app-secret": config.app_secret if config else None,
        "access-token": config.access_token if config else None,
        "phone-number-id": config.phone_number_id if config else None,
        "phone-number": config.phone_number if config else None,
        "invite-prefix": config.invite_prefix if config else None,
        "graph-version": config.graph_version if config else None,
        "mark-read": config.mark_read if config else None,
        "voice-replies": config.voice_replies if config else None,
    }
