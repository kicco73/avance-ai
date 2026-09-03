"""Tests for tracking.sources.attachment — source.attachment(name), the
first data source in the `source` namespace (see tracking.sources.
SourceNamespace / tracking.evaluation_scope.EvaluationScopeBuilder).
Reads straight from Db at the automaton's own (project_name, revision)
(see Automaton.set_storage_location) — never automaton.attachments'
in-memory copy, so every test here seeds real Archive rows instead of
building a MemoryArchive.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, State
from tracking.sources import SourceNamespace
from tracking.sources import attachment as attachment_source

pytestmark = pytest.mark.contract

PROJECT_ID = "proj"


def _seed(db, files: dict[str, bytes], content_types: dict[str, str]) -> int:
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, files, content_types)
    return db.get_project_revision(PROJECT_ID)


def _automaton(project_id: str, revision: int) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    automaton = Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action])},
        general_prompt="", signals=[], attachments={}, general_attachments={},
        autotracking_on_ai_message=False, project_id=project_id,
    )
    automaton.set_storage_location(revision)
    return automaton


def test_reads_a_text_attachment_by_its_exact_name(db):
    revision = _seed(db, {"notes.txt": b"hello world"}, {"notes.txt": "text/plain"})
    automaton = _automaton(PROJECT_ID, revision)

    assert attachment_source.read(db, automaton, "notes.txt") == "hello world"


def test_reads_a_text_attachment_by_its_unique_basename(db):
    """Same name-resolution rule build-time attachment declarations get
    (AutomatonBuilder._extract_required_archives) — reimplemented here
    against Db.list_archives instead."""
    revision = _seed(db, {"docs/notes.txt": b"nested content"}, {"docs/notes.txt": "text/plain"})
    automaton = _automaton(PROJECT_ID, revision)

    assert attachment_source.read(db, automaton, "notes.txt") == "nested content"


def test_raises_for_an_unknown_attachment_name(db):
    revision = _seed(db, {}, {})
    automaton = _automaton(PROJECT_ID, revision)

    with pytest.raises(ValueError, match="not found"):
        attachment_source.read(db, automaton, "missing.txt")


def test_raises_for_an_ambiguous_basename(db):
    revision = _seed(
        db, {"a/notes.txt": b"one", "b/notes.txt": b"two"},
        {"a/notes.txt": "text/plain", "b/notes.txt": "text/plain"},
    )
    automaton = _automaton(PROJECT_ID, revision)

    with pytest.raises(ValueError, match="ambiguous"):
        attachment_source.read(db, automaton, "notes.txt")


def test_raises_for_a_binary_attachment(db):
    revision = _seed(db, {"scan.pdf": b"%PDF-1.4..."}, {"scan.pdf": "application/pdf"})
    automaton = _automaton(PROJECT_ID, revision)

    with pytest.raises(ValueError, match="binary file"):
        attachment_source.read(db, automaton, "scan.pdf")


def test_raises_for_an_automaton_with_no_known_storage_location(db):
    """Never built through AutomatonLoader/ProjectManager — e.g. one of
    the many test-only Automaton(...) constructions elsewhere in this
    suite that never call set_storage_location at all."""
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    automaton = Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action])},
        general_prompt="", signals=[], attachments={}, general_attachments={},
        autotracking_on_ai_message=False,
    )

    with pytest.raises(ValueError, match="no known storage location"):
        attachment_source.read(db, automaton, "notes.txt")


def test_source_namespace_attachment_delegates_to_the_attachment_module(db):
    revision = _seed(db, {"notes.txt": b"via the namespace"}, {"notes.txt": "text/plain"})
    automaton = _automaton(PROJECT_ID, revision)

    assert SourceNamespace(db, automaton).attachment("notes.txt") == "via the namespace"
