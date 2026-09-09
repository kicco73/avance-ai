from __future__ import annotations

import pytest

import bus
from bus import MAIL_SEND, Message
from mail.config import parse as parse_mail_config
from tracking.actuators.actuator_set import LiveTaskNamespace

CONFIG = {
    "mail-service": {
        "url": "smtp://smtp.example.com:587",
        "username": "bot@example.com",
        "password": "secret",
    },
}


def test_parse_returns_none_when_the_section_is_absent():
    assert parse_mail_config({}, "cfg") is None


def test_parse_reads_the_required_and_optional_fields():
    config = parse_mail_config(CONFIG, "cfg")
    assert config.url == "smtp://smtp.example.com:587"
    assert config.username == "bot@example.com"
    assert config.password == "secret"
    assert config.from_name is None
    assert config.timeout_seconds == 10


def test_parse_rejects_a_missing_required_field():
    from config import ConfigError
    broken = {"mail-service": {"url": "smtp://smtp.example.com:587", "username": "bot@example.com"}}
    with pytest.raises(ConfigError):
        parse_mail_config(broken, "cfg")


def test_send_mail_raises_when_no_mail_skill_is_installed():
    with pytest.raises(ValueError, match="mail-service"):
        LiveTaskNamespace(dispatcher=None).send_mail("ada@example.com", "hi")


def test_send_mail_reaches_whatever_subscribed_to_mail_send():
    received = []

    async def fake_mail_handler(message: Message) -> None:
        received.append(message.body)

    bus.subscribe(MAIL_SEND, fake_mail_handler)

    LiveTaskNamespace(dispatcher=None).send_mail("ada@example.com", "hi")

    assert received == [{"to": "ada@example.com", "subject": "Notification from Avance", "body_md": "hi"}]
