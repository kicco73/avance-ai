"""whatsapp-service, parsed by the package that owns it.

These moved here from test_config.py when the section left AppConfig:
the core no longer knows this section exists, so what validates it is
whatsapp/config.py and what these tests call is whatsapp/config.py.
"""
from __future__ import annotations

import pytest

from config import ConfigError
from whatsapp import config as whatsapp_config

pytestmark = pytest.mark.contract

MINIMAL = {
    "whatsapp-service": {
        "enabled": True,
        "verify-token": "my-verify-token",
        "app-secret": "my-app-secret",
        "access-token": "my-access-token",
        "phone-number-id": "123456",
    },
}


def _with(**overrides) -> dict:
    return {"whatsapp-service": {**MINIMAL["whatsapp-service"], **overrides}}


def test_an_absent_section_or_enabled_false_leaves_the_channel_disabled():
    assert whatsapp_config.parse({}, "cfg") is None
    assert whatsapp_config.parse({"whatsapp-service": {"enabled": False}}, "cfg") is None


def test_enabled_parses_with_or_without_a_phone_number_normalized_to_digits_and_a_default_graph_version():
    without_phone = whatsapp_config.parse(MINIMAL, "cfg")
    assert without_phone.phone_number is None
    assert without_phone.graph_version == "v23.0"
    assert without_phone.voice_replies == "when-spoken-to"

    with_phone = whatsapp_config.parse(_with(**{"phone-number": "+34600000001"}), "cfg")
    assert with_phone.phone_number == "34600000001"


@pytest.mark.parametrize(("raw", "match"), [
    (_with(**{"phone-number": "not-a-number"}), "phone-number"),
    ({"whatsapp-service": {k: v for k, v in MINIMAL["whatsapp-service"].items() if k != "verify-token"}}, "verify-token"),
    (_with(**{"voice-replies": "sometimes"}), "voice-replies"),
])
def test_rejects_what_it_cannot_use(raw, match):
    with pytest.raises(ConfigError, match=match):
        whatsapp_config.parse(raw, "cfg")


def test_the_phone_number_id_may_arrive_as_the_int_yaml_reads_it_as():
    assert whatsapp_config.parse(_with(**{"phone-number-id": 1223547060851510}), "cfg").phone_number_id == "1223547060851510"


def test_public_fields_say_it_is_off_without_inventing_values():
    assert whatsapp_config.public_fields(None) == {
        "enabled": False, "verify-token": None, "app-secret": None, "access-token": None,
        "phone-number-id": None, "phone-number": None, "invite-prefix": None,
        "graph-version": None, "mark-read": None, "voice-replies": None,
    }
