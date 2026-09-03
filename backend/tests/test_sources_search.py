"""Tests for tracking.sources.search — source.search(what, where), the
second data source in the `source` namespace (see tracking.sources.
SourceNamespace / tracking.evaluation_scope.EvaluationScopeBuilder).
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, State
from tracking.sources import SourceNamespace
from tracking.sources import search as search_source

pytestmark = pytest.mark.contract

PROJECT_ID = "proj"

CSV = "city,country\nParis,France\nBerlin,Germany\nparis,Texas\nLondon,UK\n"


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


def test_returns_the_header_plus_every_case_insensitive_match(db):
    revision = _seed(db, {"cities.csv": CSV.encode()}, {"cities.csv": "text/csv"})
    automaton = _automaton(PROJECT_ID, revision)

    result = search_source.read(db, automaton, "paris", "cities.csv")

    assert result == "city,country\nParis,France\nparis,Texas\n"


def test_returns_just_the_header_when_nothing_matches(db):
    revision = _seed(db, {"cities.csv": CSV.encode()}, {"cities.csv": "text/csv"})
    automaton = _automaton(PROJECT_ID, revision)

    assert search_source.read(db, automaton, "Tokyo", "cities.csv") == "city,country\n"


def test_resolves_where_by_unique_basename_same_as_attachment(db):
    revision = _seed(db, {"data/cities.csv": CSV.encode()}, {"data/cities.csv": "text/csv"})
    automaton = _automaton(PROJECT_ID, revision)

    result = search_source.read(db, automaton, "Berlin", "cities.csv")

    assert result == "city,country\nBerlin,Germany\n"


def test_raises_for_an_unknown_file_same_as_attachment(db):
    revision = _seed(db, {}, {})
    automaton = _automaton(PROJECT_ID, revision)

    with pytest.raises(ValueError, match="not found"):
        search_source.read(db, automaton, "Paris", "missing.csv")


def test_returns_empty_string_for_an_empty_file(db):
    revision = _seed(db, {"empty.csv": b""}, {"empty.csv": "text/csv"})
    automaton = _automaton(PROJECT_ID, revision)

    assert search_source.read(db, automaton, "Paris", "empty.csv") == ""


def test_source_namespace_search_delegates_to_the_search_module(db):
    revision = _seed(db, {"cities.csv": CSV.encode()}, {"cities.csv": "text/csv"})
    automaton = _automaton(PROJECT_ID, revision)

    result = SourceNamespace(db, automaton).search("london", "cities.csv")

    assert result == "city,country\nLondon,UK\n"
