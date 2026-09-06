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

LABELS = {"advance": "Advance", "cancel": "Cancel"}


def test_decode_applies_every_translated_value_falling_back_per_name_the_model_skipped_or_mistranslated():
    channel = TranslateChannel(LABELS)

    assert channel.decode('{"advance": "Avanti", "cancel": "Annulla"}') == {"advance": "Avanti", "cancel": "Annulla"}
    assert channel.decode('{"advance": "Avanti"}') == {"advance": "Avanti", "cancel": "Cancel"}
    assert channel.decode('{"advance": 123}') == LABELS


@pytest.mark.parametrize("content", ["not json at all", "[1, 2, 3]", "", None], ids=["malformed", "wrong-shape", "empty", "none"])
def test_decode_never_raises_falling_back_to_every_original_label(content):
    assert TranslateChannel(LABELS).decode(content) == LABELS
