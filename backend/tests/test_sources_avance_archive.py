"""Tests for tracking.sources — the dynamic `source.<name>` namespace
(SourceNamespace) and its first driver, AvanceArchiveSource (`url:
avance:<archive path>`), source.attachment/source.search's replacement.
Reads straight from Db at the automaton's own (project_name, revision)
(see Automaton.set_storage_location) — never automaton.attachments'
in-memory copy, so every test here seeds real Archive rows instead of
building a MemoryArchive.

select() is the only method left on this driver (see SourceDriver's own
docstring on why a whole-file read isn't a source.* capability at all
anymore) — every test below that used to call read() to exercise the
shared _read_text/_read_canonical machinery (the content-type guard, the
per-session cache) now goes through select() instead: for a single-line
file, `select()` still returns that one line verbatim (its own "header"
is always included, unconditionally), so the same assertions hold.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, Source, State
from tracking.sources import SourceNamespace
from tracking.sources.avance_archive import AvanceArchiveSource
from tracking.sources.base import MAX_SOURCE_RESULT_CHARS
from tracking.sources.url import parse_source_url

pytestmark = pytest.mark.contract

PROJECT_ID = "proj"

CSV = "city,country\nParis,France\nBerlin,Germany\nparis,Texas\nLondon,UK\n"


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
    return AvanceArchiveSource(db, automaton, name, archive_path, session_id=session_id)


def test_parse_source_url_splits_scheme_and_path():
    assert parse_source_url("avance:behaviour/flights.csv") == ("avance", "behaviour/flights.csv")


def test_parse_source_url_rejects_a_url_with_no_scheme_or_path():
    with pytest.raises(ValueError):
        parse_source_url("no-colon-here")
    with pytest.raises(ValueError):
        parse_source_url(":no-scheme")
    with pytest.raises(ValueError):
        parse_source_url("avance:")


def test_select_raises_for_an_unknown_archive_path(db):
    revision = _seed(db, {}, {})
    automaton = _automaton(PROJECT_ID, revision)

    with pytest.raises(ValueError):
        _driver(automaton, db, "notes.txt").select("x")


def test_select_raises_for_a_binary_archive(db):
    revision = _seed(db, {"logo.png": b"\x89PNG"}, {"logo.png": "image/png"})
    automaton = _automaton(PROJECT_ID, revision)

    with pytest.raises(ValueError):
        _driver(automaton, db, "logo.png").select("x")


def test_select_returns_the_header_plus_every_case_insensitive_match(db):
    revision = _seed(db, {"cities.csv": CSV.encode()}, {"cities.csv": "text/csv"})
    automaton = _automaton(PROJECT_ID, revision)

    result = _driver(automaton, db, "cities.csv").select("paris")

    assert result == "city,country\nParis,France\nparis,Texas\n"


def test_select_returns_just_the_header_when_nothing_matches(db):
    revision = _seed(db, {"cities.csv": CSV.encode()}, {"cities.csv": "text/csv"})
    automaton = _automaton(PROJECT_ID, revision)

    assert _driver(automaton, db, "cities.csv").select("Tokyo") == "city,country\n"


def test_select_result_beyond_the_char_limit_is_truncated_with_a_final_line(db):
    # One header row plus enough matching rows to blow well past
    # MAX_SOURCE_RESULT_CHARS — every source.*/tool result is bounded
    # the same way, regardless of caller (see SourceDriver._bounded).
    rows = "\n".join(f"paris-row-{i}" for i in range(MAX_SOURCE_RESULT_CHARS))
    content = f"header\n{rows}\n"
    revision = _seed(db, {"big.csv": content.encode()}, {"big.csv": "text/csv"})
    automaton = _automaton(PROJECT_ID, revision)

    result = _driver(automaton, db, "big.csv").select("paris")

    assert len(result) <= MAX_SOURCE_RESULT_CHARS + len("\n[truncated: 999999999 more characters]")
    assert result.startswith("header\nparis-row-0\n")
    last_line = result.splitlines()[-1]
    assert last_line.startswith("[truncated: ") and last_line.endswith(" more characters]")


def test_create_update_delete_no_longer_exist_on_a_source_driver(db):
    # Not "unsupported" (the base class's own default for an operation a
    # driver opts out of) — gone entirely, from SourceDriver itself, not
    # just this one driver. select is the only method left at all.
    revision = _seed(db, {"notes.txt": b"hello"}, {"notes.txt": "text/plain"})
    automaton = _automaton(PROJECT_ID, revision)
    driver = _driver(automaton, db, "notes.txt")

    assert not hasattr(driver, "create")
    assert not hasattr(driver, "update")
    assert not hasattr(driver, "delete")
    assert not hasattr(driver, "read")


def test_source_namespace_resolves_a_declared_name_to_its_driver(db):
    revision = _seed(db, {"notes.txt": b"hello from the archive"}, {"notes.txt": "text/plain"})
    automaton = _automaton(PROJECT_ID, revision, sources=[Source(name="pino", url="avance:notes.txt", ui_label="pino")])

    resolved = SourceNamespace(db, automaton).pino

    assert isinstance(resolved, AvanceArchiveSource)
    assert resolved.select("archive") == "hello from the archive"


def test_source_namespace_raises_for_an_undeclared_name(db):
    revision = _seed(db, {}, {})
    automaton = _automaton(PROJECT_ID, revision, sources=[])

    with pytest.raises(ValueError):
        SourceNamespace(db, automaton).nope


class TestPerSessionReadCache:
    """With no session_id (a wake-up re-evaluation, a test replay),
    select() goes straight to the canonical archive and never writes a
    cache copy — same behavior as before this cache existed. With one,
    it reads through cache/sessions/<id>/<archive path> instead,
    duplicating the canonical content into it on a miss."""

    def test_no_session_id_reads_canonical_directly_and_writes_no_cache_copy(self, db):
        revision = _seed(db, {"notes.txt": b"hello"}, {"notes.txt": "text/plain"})
        automaton = _automaton(PROJECT_ID, revision)

        assert _driver(automaton, db, "notes.txt", session_id=None).select("hello") == "hello"
        assert not any(name.startswith("cache/") for name in db.list_archives(PROJECT_ID, revision=revision))

    def test_a_session_id_duplicates_the_canonical_content_into_its_own_cache_copy_on_first_read(self, db):
        revision = _seed(db, {"notes.txt": b"hello"}, {"notes.txt": "text/plain"})
        automaton = _automaton(PROJECT_ID, revision)

        assert _driver(automaton, db, "notes.txt", session_id=42).select("hello") == "hello"

        cached = db.get_archive(PROJECT_ID, "cache/sessions/42/notes.txt", revision=revision)
        assert cached == b"hello"

    def test_a_cache_hit_is_read_straight_from_its_own_copy_without_touching_the_canonical_content(self, db):
        revision = _seed(db, {"notes.txt": b"original"}, {"notes.txt": "text/plain"})
        automaton = _automaton(PROJECT_ID, revision)
        _driver(automaton, db, "notes.txt", session_id=42).select("x")  # populates the cache copy

        # The project gets edited/republished underneath the still-open
        # session — the canonical archive now reads differently...
        db.save_project_files(PROJECT_ID, {"notes.txt": b"edited"}, {"notes.txt": "text/plain"})

        # ...but this session's own cached copy is untouched, so it keeps
        # seeing exactly what it first read, all conversation long.
        assert _driver(automaton, db, "notes.txt", session_id=42).select("x") == "original"

    def test_two_different_sessions_get_their_own_independent_cache_copies(self, db):
        revision = _seed(db, {"notes.txt": b"hello"}, {"notes.txt": "text/plain"})
        automaton = _automaton(PROJECT_ID, revision)

        _driver(automaton, db, "notes.txt", session_id=1).select("x")
        _driver(automaton, db, "notes.txt", session_id=2).select("x")

        assert db.get_archive(PROJECT_ID, "cache/sessions/1/notes.txt", revision=revision) == b"hello"
        assert db.get_archive(PROJECT_ID, "cache/sessions/2/notes.txt", revision=revision) == b"hello"

    def test_select_also_reads_through_the_session_cache(self, db):
        revision = _seed(db, {"cities.csv": CSV.encode()}, {"cities.csv": "text/csv"})
        automaton = _automaton(PROJECT_ID, revision)

        result = _driver(automaton, db, "cities.csv", session_id=7).select("paris")

        assert result == "city,country\nParis,France\nparis,Texas\n"
        assert db.get_archive(PROJECT_ID, "cache/sessions/7/cities.csv", revision=revision) == CSV.encode()
