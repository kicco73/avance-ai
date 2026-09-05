"""TranslateChannel.decode is deliberately lenient — a name the model
skips, mistranslates into a non-string, or loses entirely to a malformed
response must fall back to its own original text rather than raise or
leave a gap: an untranslated label beats a missing one, and this is a UX
nicety layered on top of an otherwise-complete reply, never core protocol
correctness like signals/env.
"""
from __future__ import annotations

import pytest

from tracking.channels import TranslateChannel

pytestmark = pytest.mark.contract


def test_decode_applies_every_translated_value():
    channel = TranslateChannel({"advance": "Advance", "cancel": "Cancel"})
    result = channel.decode('{"advance": "Avanti", "cancel": "Annulla"}')
    assert result == {"advance": "Avanti", "cancel": "Annulla"}


def test_decode_falls_back_to_the_original_for_a_name_the_model_omitted():
    channel = TranslateChannel({"advance": "Advance", "cancel": "Cancel"})
    result = channel.decode('{"advance": "Avanti"}')
    assert result == {"advance": "Avanti", "cancel": "Cancel"}


def test_decode_falls_back_to_the_original_for_a_non_string_value():
    channel = TranslateChannel({"advance": "Advance"})
    result = channel.decode('{"advance": 123}')
    assert result == {"advance": "Advance"}


def test_decode_of_malformed_json_falls_back_to_every_original():
    channel = TranslateChannel({"advance": "Advance", "cancel": "Cancel"})
    result = channel.decode("not json at all")
    assert result == {"advance": "Advance", "cancel": "Cancel"}


def test_decode_of_empty_content_falls_back_to_every_original():
    channel = TranslateChannel({"advance": "Advance"})
    assert channel.decode("") == {"advance": "Advance"}
    assert channel.decode(None) == {"advance": "Advance"}


def test_decode_never_raises():
    channel = TranslateChannel({"advance": "Advance"})
    # A JSON array, not an object — structurally wrong, still no raise.
    assert channel.decode("[1, 2, 3]") == {"advance": "Advance"}
