"""Tests for tracking.sources — the dynamic `source.<name>` namespace
(SourceNamespace) and its first driver, AvanceArchiveSource (`url:
avance:<archive path>`), source.attachment/source.search's replacement.
Reads straight from Db at the automaton's own (project_name, revision)
(see Automaton.set_storage_location) — never automaton.attachments'
in-memory copy, so every test here seeds real Archive rows instead of
building a MemoryArchive.

select()/value() are the only methods this driver implements (see
SourceDriver's own docstring on why a whole-file read isn't a source.*
capability at all — and `update`, part of the uniform interface, stays
unsupported here).
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, Source, State
from tracking.env import Env
from tracking.sources import SourceNamespace
from tracking.sources.avance_archive import AvanceArchiveSource
from tracking.sources.base import MAX_SOURCE_RESULT_CHARS, SourceContext
from tracking.sources.url import parse_source_url

pytestmark = pytest.mark.contract

PROJECT_ID = "proj"

CSV = "city,country\nParis,France\nBerlin,Germany\nparis,Texas\nLondon,UK\n"
FLIGHTS = "codice_volo,data_partenza,datetime_partenza_reale\nVY3003,2026-08-16,2026-08-16 07:12\nVY3003,2026-08-17,2026-08-17 07:05\n"


def _seed(db, files: dict[str, bytes], content_types: dict[str, str]) -> int:
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, files, content_types)
    return db.get_project_revision(PROJECT_ID)


def _automaton(project_id: str, revision: int, sources: list[Source] | None = None) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    automaton = Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action])},
        general_prompt="", signals=[], attachments={}, general_attachments={},
        autotracking_on_ai_message=False, project_id=project_id, sources=sources,
    )
    automaton.set_storage_location(revision)
    return automaton


def _driver(
    automaton: Automaton, db, archive_path: str, name: str = "pino", session_id: int | None = None,
) -> AvanceArchiveSource:
    context = SourceContext(db=db, automaton=automaton, session_id=session_id, env=Env())
    return AvanceArchiveSource(context, name, archive_path)


def _seeded_driver(db, name: str, content: str, content_type: str = "text/csv", session_id: int | None = None):
    revision = _seed(db, {name: content.encode()}, {name: content_type})
    return _driver(_automaton(PROJECT_ID, revision), db, name, session_id=session_id), revision


def test_parse_source_url_splits_scheme_and_path_rejecting_a_missing_scheme_or_path():
    assert parse_source_url("avance:behaviour/flights.csv") == ("avance", "behaviour/flights.csv")
    for bad in ("no-colon-here", ":no-scheme", "avance:"):
        with pytest.raises(ValueError):
            parse_source_url(bad)


def test_select_raises_for_an_unknown_or_binary_archive(db):
    revision = _seed(db, {"logo.png": b"\x89PNG"}, {"logo.png": "image/png"})
    automaton = _automaton(PROJECT_ID, revision)

    with pytest.raises(ValueError):
        _driver(automaton, db, "notes.txt").select("x")
    with pytest.raises(ValueError):
        _driver(automaton, db, "logo.png").select("x")


def test_select_returns_the_header_plus_every_case_insensitive_match_or_the_empty_string_or_every_row(db):
    # "" means "not found," full stop — the header only ever appears
    # alongside at least one matching row (see SourceDriver.select's own
    # docstring), so `select(...) != ''` is a real existence check.
    driver, _ = _seeded_driver(db, "cities.csv", CSV)

    assert driver.select("paris") == "city,country\nParis,France\nparis,Texas\n"
    assert driver.select("London") == "city,country\nLondon,UK\n"
    assert driver.select("Tokyo") == ""
    assert driver.select() == CSV


def test_select_result_beyond_the_char_limit_is_refused_with_the_header_still_attached(db):
    # One header row plus enough matching rows to blow well past
    # MAX_SOURCE_RESULT_CHARS — every source.*/tool result is bounded
    # the same way, regardless of caller (see SourceDriver._bounded). The
    # header rides along on the refusal so the model still knows the
    # field names to narrow its next query by.
    rows = "\n".join(f"paris-row-{i}" for i in range(MAX_SOURCE_RESULT_CHARS))
    driver, _ = _seeded_driver(db, "big.csv", f"header\n{rows}\n")

    assert driver.select("paris") == "error: response too long — provide more specific filters, then try again.\nheader"


def test_select_ands_several_values_together_while_one_value_returns_every_match(db):
    driver, _ = _seeded_driver(db, "flights.csv", "code,date\nVY3003,2026-06-01\nVY3003,2026-06-02\nVY4000,2026-06-01\n")

    assert driver.select("VY3003", "2026-06-01") == "code,date\nVY3003,2026-06-01\n"
    assert driver.select("VY3003") == "code,date\nVY3003,2026-06-01\nVY3003,2026-06-02\n"


def test_create_delete_and_read_do_not_exist_and_update_is_unsupported_on_the_archive_driver(db):
    # create/delete/read are gone entirely, from SourceDriver itself.
    # update is part of the uniform interface (an avance:env source
    # implements it) but this driver opts out — the base class's own
    # "not supported" default, never a silent no-op.
    driver, _ = _seeded_driver(db, "notes.txt", "hello", content_type="text/plain")

    assert not hasattr(driver, "create")
    assert not hasattr(driver, "delete")
    assert not hasattr(driver, "read")
    assert "update" not in AvanceArchiveSource.SUPPORTED_METHODS
    with pytest.raises(ValueError, match="source.pino.update.*not supported"):
        driver.update(fields={"a": "b"})


def test_keys_project_the_matching_rows_onto_the_named_columns_in_the_order_asked_keeping_the_files_delimiter(db):
    driver, _ = _seeded_driver(db, "flights.csv", FLIGHTS)

    assert driver.select("VY3003", "2026-08-16", keys=["data_partenza"]) == "data_partenza\n2026-08-16\n"
    assert driver.select("VY3003", keys=["data_partenza", "codice_volo"]) == "data_partenza,codice_volo\n2026-08-16,VY3003\n2026-08-17,VY3003\n"
    assert driver.select("2026-08-17") == "codice_volo,data_partenza,datetime_partenza_reale\nVY3003,2026-08-17,2026-08-17 07:05\n"

    unknown = driver.select("VY3003", keys=["nope"])
    assert unknown.startswith("error: unknown column(s) 'nope'")
    assert "codice_volo, data_partenza, datetime_partenza_reale" in unknown

    semicolons, _ = _seeded_driver(db, "flights.csv", "code;city\nVY1;Paris\nVY2;Rome\n")
    assert semicolons.select("VY", keys=["city"]) == "city\nParis\nRome\n"


def test_value_returns_the_key_cell_of_the_first_matching_row_or_the_empty_string_reporting_an_unknown_column_as_text(db):
    driver, _ = _seeded_driver(db, "flights.csv", "codice_volo,data_partenza\nVY3003,2026-08-16\nVY3003,2026-08-17\nVY4000,2026-08-16\n")

    assert driver.value("VY3003", key="data_partenza") == "2026-08-16"
    assert driver.value("VY9999", key="data_partenza") == ""
    assert driver.value("VY3003", key="nope").startswith("error: unknown column(s) 'nope'")


def test_source_namespace_resolves_a_declared_name_to_its_driver_and_raises_for_an_undeclared_one(db):
    revision = _seed(db, {"notes.txt": b"note\nhello from the archive\n"}, {"notes.txt": "text/plain"})
    automaton = _automaton(PROJECT_ID, revision, sources=[Source(name="pino", url="avance:notes.txt", ui_label="pino")])

    resolved = SourceNamespace(db, automaton).pino
    assert isinstance(resolved, AvanceArchiveSource)
    assert resolved.select("archive") == "note\nhello from the archive\n"

    with pytest.raises(ValueError):
        SourceNamespace(db, automaton).nope


class TestPerSessionReadCache:
    """With no session_id (a wake-up re-evaluation, a test replay),
    select() goes straight to the canonical archive and never writes a
    cache copy. With one, it reads through cache/sessions/<id>/<archive
    path> instead, duplicating the canonical content into it on a miss."""

    def test_a_session_reads_through_its_own_cache_copy_frozen_at_first_read_while_no_session_reads_canonical(self, db):
        revision = _seed(db, {"notes.txt": b"note\noriginal\n"}, {"notes.txt": "text/plain"})
        automaton = _automaton(PROJECT_ID, revision)

        assert _driver(automaton, db, "notes.txt", session_id=None).select("original") == "note\noriginal\n"
        assert not any(name.startswith("cache/") for name in db.list_archives(PROJECT_ID, revision=revision))

        assert _driver(automaton, db, "notes.txt", session_id=42).select("original") == "note\noriginal\n"
        assert db.get_archive(PROJECT_ID, "cache/sessions/42/notes.txt", revision=revision) == b"note\noriginal\n"

        # The project gets edited/republished underneath the still-open
        # session — the canonical archive now reads differently, but this
        # session's own cached copy keeps seeing exactly what it first read.
        db.save_project_files(PROJECT_ID, {"notes.txt": b"note\nedited\n"}, {"notes.txt": "text/plain"})
        assert _driver(automaton, db, "notes.txt", session_id=42).select("original") == "note\noriginal\n"
        assert _driver(automaton, db, "notes.txt", session_id=None).select("edited") == "note\nedited\n"

    def test_different_sessions_get_independent_cache_copies_and_select_matches_through_them(self, db):
        revision = _seed(db, {"cities.csv": CSV.encode()}, {"cities.csv": "text/csv"})
        automaton = _automaton(PROJECT_ID, revision)

        assert _driver(automaton, db, "cities.csv", session_id=1).select("paris") == "city,country\nParis,France\nparis,Texas\n"
        _driver(automaton, db, "cities.csv", session_id=2).select("x")

        assert db.get_archive(PROJECT_ID, "cache/sessions/1/cities.csv", revision=revision) == CSV.encode()
        assert db.get_archive(PROJECT_ID, "cache/sessions/2/cities.csv", revision=revision) == CSV.encode()
