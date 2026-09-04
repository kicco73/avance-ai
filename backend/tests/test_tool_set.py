"""ToolSet — the model's own callable catalog for a state's declared
`tools:` (see automaton.State.tools), built by
SourceNamespace.tool_set(names): one ToolSpec per (named source,
SourceDriver method), and call() resolving through the same
SourceNamespace (and so the same per-session read cache) a
source.<name>.<method>() expression already uses.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, Source, State
from db.db import Db
from tracking.sources import SourceNamespace

pytestmark = pytest.mark.contract

PROJECT_ID = "proj"


@pytest.fixture
def file_db(tmp_path) -> Db:
    # File-backed, not :memory: — ToolSet.call() runs the driver via
    # asyncio.to_thread, and a second thread's own connection to
    # ":memory:" would see a distinct, empty database instead of shared
    # state (see conftest.py's own app_db/test_on_enter_task.py's own
    # file_db, the same concern for a real background job-worker thread).
    return Db(f"sqlite:///{tmp_path / 'tool_set.db'}")


def _seed(db, files: dict[str, bytes], content_types: dict[str, str]) -> int:
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, files, content_types)
    return db.get_project_revision(PROJECT_ID)


def _automaton(project_id: str, revision: int, sources: list[Source]) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    automaton = Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action])},
        general_prompt="", signals=[], attachments={}, general_attachments={},
        autotracking_on_ai_message=False, project_id=project_id, sources=sources,
    )
    automaton.set_storage_location(revision)
    return automaton


def _two_sources(db) -> Automaton:
    revision = _seed(
        db,
        {"flights.csv": b"city,country\nParis,France\n", "tickets.csv": b"id,seat\n1,12A\n"},
        {"flights.csv": "text/csv", "tickets.csv": "text/csv"},
    )
    sources = [
        Source(name="flights", url="avance:flights.csv", ui_label="Flights", ui_description="Flight records."),
        Source(name="tickets", url="avance:tickets.csv", ui_label="Tickets"),
    ]
    return _automaton(PROJECT_ID, revision, sources)


def test_specs_covers_every_named_source_and_every_supported_method(db):
    automaton = _two_sources(db)
    tool_set = SourceNamespace(db, automaton).tool_set(["flights", "tickets"])

    names = {spec.name for spec in tool_set.specs()}
    assert names == {"source_flights_read", "source_flights_select", "source_tickets_read", "source_tickets_select"}


def test_only_the_named_sources_are_included_even_when_more_are_declared(db):
    automaton = _two_sources(db)
    tool_set = SourceNamespace(db, automaton).tool_set(["flights"])

    names = {spec.name for spec in tool_set.specs()}
    assert names == {"source_flights_read", "source_flights_select"}


def test_an_unknown_name_passed_to_tool_set_raises(db):
    automaton = _two_sources(db)

    with pytest.raises(ValueError, match="source.nope"):
        SourceNamespace(db, automaton).tool_set(["nope"])


def test_description_combines_the_method_blurb_and_the_source_s_own_ui_description(db):
    automaton = _two_sources(db)
    tool_set = SourceNamespace(db, automaton).tool_set(["flights"])

    select_spec = next(spec for spec in tool_set.specs() if spec.name == "source_flights_select")
    assert "Grep over this source" in select_spec.description
    assert "Flight records." in select_spec.description


def test_description_is_just_the_method_blurb_when_the_source_has_no_ui_description(db):
    automaton = _two_sources(db)
    tool_set = SourceNamespace(db, automaton).tool_set(["tickets"])

    select_spec = next(spec for spec in tool_set.specs() if spec.name == "source_tickets_select")
    assert select_spec.description == select_spec.description.strip()
    assert "\n\n" not in select_spec.description


def test_every_parameter_is_a_required_string_select_takes_one_read_takes_none(db):
    automaton = _two_sources(db)
    tool_set = SourceNamespace(db, automaton).tool_set(["flights"])
    specs = {spec.name: spec for spec in tool_set.specs()}

    assert specs["source_flights_select"].parameters == {
        "type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"],
    }
    assert specs["source_flights_read"].parameters == {"type": "object", "properties": {}, "required": []}


async def test_call_resolves_to_the_named_source_s_own_driver(file_db):
    automaton = _two_sources(file_db)
    tool_set = SourceNamespace(file_db, automaton).tool_set(["flights", "tickets"])

    assert await tool_set.call("source_flights_select", {"value": "paris"}) == "city,country\nParis,France\n"
    assert await tool_set.call("source_tickets_read", {}) == "id,seat\n1,12A\n"


async def test_call_with_an_unknown_tool_name_returns_an_error_string_not_an_exception(db):
    automaton = _two_sources(db)
    tool_set = SourceNamespace(db, automaton).tool_set(["flights"])

    result = await tool_set.call("source_nope_select", {"value": "x"})

    assert result == "error: unknown tool 'source_nope_select'."


async def test_call_a_driver_exception_comes_back_as_an_error_string_too(file_db):
    automaton = _two_sources(file_db)
    tool_set = SourceNamespace(file_db, automaton).tool_set(["flights"])

    # 'value' is required by select() — omitting it raises inside the
    # driver call, which call() must turn into a string, never propagate.
    result = await tool_set.call("source_flights_select", {})

    assert result.startswith("error:")


async def test_call_never_blocks_the_event_loop(file_db):
    """The driver call runs synchronous disk/DB I/O — asyncio.to_thread
    is what keeps it off the loop; a crude proxy for that here is just
    that awaiting it actually yields a real result without the test
    itself needing a thread of its own."""
    automaton = _two_sources(file_db)
    tool_set = SourceNamespace(file_db, automaton).tool_set(["flights"])

    result = await tool_set.call("source_flights_read", {})

    assert result == "city,country\nParis,France\n"
