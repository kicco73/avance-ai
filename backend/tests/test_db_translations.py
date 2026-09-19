from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


def test_a_saved_translation_round_trips_by_its_own_key_lang_and_text(db):
    db.save_translation("slot", "en-US", "morning", "it-IT", "mattina")

    assert db.get_translation("slot", "en-US", "morning", "it-IT") == "mattina"
    assert db.get_translation("slot", "en-US", "morning", "es-ES") is None
    assert db.get_translation("other", "en-US", "morning", "it-IT") is None


def test_saving_the_same_key_lang_and_text_again_overwrites_rather_than_duplicates(db):
    db.save_translation("slot", "en-US", "morning", "it-IT", "mattina")
    db.save_translation("slot", "en-US", "morning", "it-IT", "di mattina")

    assert db.get_translation("slot", "en-US", "morning", "it-IT") == "di mattina"
    assert db.count_translations() == 1


def test_clear_translations_deletes_everything_and_reports_how_many(db):
    db.save_translation("slot", "en-US", "morning", "it-IT", "mattina")
    db.save_translation("slot", "en-US", "evening", "it-IT", "sera")

    deleted = db.clear_translations()

    assert deleted == 2
    assert db.count_translations() == 0
    assert db.get_translation("slot", "en-US", "morning", "it-IT") is None
