"""ToolSet — the model's own callable catalog for a state's declared
`ai-may-read-sources:`/`ai-must-read-sources:`/`ai-may-write-sources:` (see
automaton.State), built by SourceNamespace.tool_set(may_read, must_read,
may_write): one `select` ToolSpec per read source, one `update` per write
source, and call() resolving through the same SourceNamespace (and so the
same per-session read cache and Env) a source.<name>.<method>() expression
already uses.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, EnvKey, Source, State
from db.db import Db
from db.models import Tracking
from tracking.env import Env, PersistedEnv
from tracking.fixed_project_context import FixedProjectContext
from tracking.sources import METHOD_SCHEMAS, SourceNamespace

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


def _automaton(
    project_id: str, revision: int, sources: list[Source], env_keys: list[EnvKey] | None = None,
) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    automaton = Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action])},
        general_prompt="", signals=[], attachments={}, general_attachments={},
        autotracking_on_ai_message=False, project_id=project_id, sources=sources, env_keys=env_keys,
    )
    automaton.set_storage_location(revision)
    return automaton


ENV_SOURCE = Source(name="env", url="avance:env", ui_label="Env", ai_definition="The automaton's variables.")
ENV_KEYS = [
    EnvKey(name="pnr", ai_access="readwrite", ai_definition="The record locator."),
    EnvKey(name="customer_email", ai_access="readonly", ai_definition="The customer's email."),
    EnvKey(name="_hidden"),
]


def _env_automaton(db) -> Automaton:
    return _automaton(PROJECT_ID, _seed(db, {}, {}), [ENV_SOURCE], ENV_KEYS)


def _two_sources(db) -> Automaton:
    revision = _seed(
        db,
        {"flights.csv": b"city,country\nParis,France\n", "tickets.csv": b"id,seat\n1,12A\n"},
        {"flights.csv": "text/csv", "tickets.csv": "text/csv"},
    )
    sources = [
        Source(name="flights", url="avance:flights.csv", ui_label="Flights", ai_definition="Flight records."),
        Source(name="tickets", url="avance:tickets.csv", ui_label="Tickets"),
    ]
    return _automaton(PROJECT_ID, revision, sources)


def _flights_automaton(db, csv: bytes, **source_fields) -> Automaton:
    revision = _seed(db, {"flights.csv": csv}, {"flights.csv": "text/csv"})
    return _automaton(PROJECT_ID, revision, [Source(name="flights", url="avance:flights.csv", ui_label="Flights", **source_fields)])


def _names(tool_set) -> set[str]:
    return {spec.name for spec in tool_set.specs()}


def test_specs_cover_only_the_named_sources_with_select_and_update_but_never_value(db):
    two = _two_sources(db)
    assert _names(SourceNamespace(db, two).tool_set(["flights", "tickets"])) == {"source_flights_select", "source_tickets_select"}
    assert _names(SourceNamespace(db, two).tool_set(["flights"])) == {"source_flights_select"}

    # AvanceEnvSource.SUPPORTED_METHODS includes "value" (scripts/triggers
    # only) — ToolSet must still only ever wire up select/update.
    env_names = _names(SourceNamespace(db, _env_automaton(db), env=Env()).tool_set(["env"], may_write_names=["env"]))
    assert env_names == {"source_env_select", "source_env_update"}
    assert not any(name.endswith("_value") for name in env_names)


def test_an_unknown_name_anywhere_or_a_write_on_a_driver_without_update_raises(db):
    automaton = _two_sources(db)

    with pytest.raises(ValueError, match="source.nope"):
        SourceNamespace(db, automaton).tool_set(["nope"])
    with pytest.raises(ValueError, match="source.nope"):
        SourceNamespace(db, automaton).tool_set([], ["nope"])
    with pytest.raises(ValueError, match="source.flights.update"):
        SourceNamespace(db, automaton).tool_set([], [], ["flights"])


def test_description_is_the_method_blurb_plus_the_sources_own_ai_definition_never_its_ui_description(db):
    """ui-description is written for the human (the Inspector/design
    view); ai-definition is written for the model. Only the latter may
    ever reach a ToolSpec."""
    two = _two_sources(db)
    flights = next(spec for spec in SourceNamespace(db, two).tool_set(["flights"]).specs())
    assert "Grep over this source" in flights.description
    assert "Flight records." in flights.description

    tickets = next(spec for spec in SourceNamespace(db, two).tool_set(["tickets"]).specs())
    assert tickets.description == tickets.description.strip()
    assert "\n\n" not in tickets.description

    both = _flights_automaton(db, b"a,b\n1,2\n", ui_description="Shown in the Inspector only.", ai_definition="Read by the model only.")
    spec = next(spec for spec in SourceNamespace(db, both).tool_set(["flights"]).specs())
    assert "Read by the model only." in spec.description
    assert "Shown in the Inspector only." not in spec.description


def test_parameter_schemas_are_the_uniform_method_schemas_unless_the_driver_narrows_them(db):
    select = next(spec for spec in SourceNamespace(db, _two_sources(db)).tool_set(["flights"]).specs()).parameters
    assert select is METHOD_SCHEMAS["select"]
    assert select["required"] == ["values"]
    assert select["properties"]["values"]["type"] == "array"
    assert "minItems" not in select["properties"]["values"]
    assert select["properties"]["keys"]["items"] == {"type": "string"}

    update = METHOD_SCHEMAS["update"]
    assert update["required"] == ["values", "fields"]
    assert update["properties"]["fields"]["type"] == "object"
    assert update["properties"]["fields"]["additionalProperties"] == {"type": "string"}
    assert update["properties"]["fields"]["minProperties"] == 1

    specs = {spec.name: spec for spec in SourceNamespace(db, _env_automaton(db), env=Env()).tool_set(["env"], [], ["env"]).specs()}
    narrowed_select = specs["source_env_select"].parameters
    assert narrowed_select["properties"]["keys"]["items"]["enum"] == ["pnr", "customer_email"]
    narrowed_update = specs["source_env_update"].parameters
    assert set(narrowed_update["properties"]["fields"]["properties"]) == {"pnr"}
    assert narrowed_update["properties"]["fields"]["properties"]["pnr"]["description"] == "The record locator."
    assert narrowed_update["properties"]["fields"]["additionalProperties"] is False
    assert narrowed_update["properties"]["fields"]["minProperties"] == 1
    assert narrowed_update["required"] == ["values", "fields"]


def test_required_specs_cover_only_the_must_sources_and_only_ever_their_select(db):
    """`must` forces a read only — a write is never forced."""
    two = _two_sources(db)
    assert SourceNamespace(db, two).tool_set(["flights", "tickets"]).required_specs() == []

    mixed = SourceNamespace(db, two).tool_set(["flights"], ["tickets"])
    assert _names(mixed) == {"source_flights_select", "source_tickets_select"}
    assert {spec.name for spec in mixed.required_specs()} == {"source_tickets_select"}

    env = SourceNamespace(db, _env_automaton(db), env=Env()).tool_set([], ["env"], ["env"])
    assert _names(env) == {"source_env_select", "source_env_update"}
    assert {spec.name for spec in env.required_specs()} == {"source_env_select"}


async def test_call_routes_to_the_named_sources_own_driver_passing_keys_by_keyword_and_narrowing_on_every_value(file_db):
    two = SourceNamespace(file_db, _two_sources(file_db)).tool_set(["flights", "tickets"])
    assert await two.call("source_flights_select", {"values": ["paris"]}) == "city,country\nParis,France\n"
    assert await two.call("source_tickets_select", {"values": ["12A"]}) == "id,seat\n1,12A\n"

    keyed = SourceNamespace(file_db, _flights_automaton(file_db, b"code,date,city\nVY3003,2026-06-01,Paris\n")).tool_set(["flights"])
    assert await keyed.call("source_flights_select", {"values": ["VY3003"], "keys": ["city", "code"]}) == "city,code\nParis,VY3003\n"

    narrowed = SourceNamespace(file_db, _flights_automaton(file_db, b"code,date\nVY3003,2026-06-01\nVY3003,2026-06-02\n")).tool_set(["flights"])
    assert await narrowed.call("source_flights_select", {"values": ["VY3003", "2026-06-01"]}) == "code,date\nVY3003,2026-06-01\n"


async def test_call_routes_an_update_to_the_driver_writing_the_env_with_origin_tool_injected_never_from_the_model(file_db):
    # origin is never part of any tool's own JSON schema (the model can't
    # see or spoof it) — ToolSet.call injects it itself, in Python, only
    # for a write (see its own docstring).
    env = Env()
    tool_set = SourceNamespace(file_db, _env_automaton(file_db), env=env).tool_set(["env"], [], ["env"])
    assert await tool_set.call("source_env_update", {"values": [], "fields": {"pnr": "ABC123"}}) == "1 row updated"
    assert env.action_set() == {"pnr": "ABC123"}

    file_db.publish_project(PROJECT_ID)
    session_id = file_db.create_chat_session(
        username="user", project_id=PROJECT_ID, revision=file_db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1), start_state="a", end_state="a",
    )
    persisted = PersistedEnv(file_db, FixedProjectContext(project_id=PROJECT_ID), session_id)
    tool_set = SourceNamespace(file_db, _env_automaton(file_db), env=persisted).tool_set(["env"], [], ["env"])

    await tool_set.call("source_env_update", {"values": [], "fields": {"pnr": "ABC123"}})

    assert Tracking.get(Tracking.action_env.is_null(False)).origin == "tool"


async def test_call_turns_an_unknown_tool_or_a_driver_exception_into_an_error_string_never_an_exception(file_db):
    tool_set = SourceNamespace(file_db, _two_sources(file_db)).tool_set(["flights"])
    result = await tool_set.call("source_nope_select", {"values": ["x"]})
    assert result.startswith("error: unknown tool 'source_nope_select'.")

    # ghost's own archive file was never seeded — select() raises inside
    # the driver call, which call() must turn into a string, never propagate.
    revision = _seed(file_db, {"flights.csv": b"city,country\nParis,France\n"}, {"flights.csv": "text/csv"})
    ghost = SourceNamespace(file_db, _automaton(PROJECT_ID, revision, [Source(name="ghost", url="avance:missing.csv", ui_label="Ghost")])).tool_set(["ghost"])
    assert (await ghost.call("source_ghost_select", {"values": ["x"]})).startswith("error:")


def test_tool_event_start_carries_the_sources_own_label_and_description_falling_back_to_ui_label_and_none(db):
    described = _automaton(PROJECT_ID, _seed(db, {}, {}), [Source(
        name="flights", url="avance:flights.csv", ui_label="Flights", ui_description="Shown in the Inspector.",
    )])
    event = SourceNamespace(db, described).tool_set(["flights"]).tool_event("source_flights_select", {"values": ["VY3003"]}, "start", round=1)
    assert event == {
        "phase": "start", "name": "source_flights_select", "source": "flights", "method": "select",
        "label": "Flights", "description": "Shown in the Inspector.", "arguments": {"values": ["VY3003"]}, "round": 1,
    }

    bare = SourceNamespace(db, _two_sources(db)).tool_set(["tickets"]).tool_event("source_tickets_select", {"values": []}, "start", round=1)
    assert bare["label"] == "Tickets"
    assert bare["description"] is None


def test_tool_event_result_adds_result_rows_error_and_duration_with_zero_rows_when_empty_or_refused(db):
    tool_set = SourceNamespace(db, _two_sources(db)).tool_set(["flights"])

    event = tool_set.tool_event(
        "source_flights_select", {"values": ["VY3003"]}, "result",
        round=1, result="city,country\nParis,France\nOrly,France\n", duration_ms=12,
    )
    assert event["result"] == "city,country\nParis,France\nOrly,France\n"
    assert event["rows"] == 2
    assert event["error"] is False
    assert event["duration_ms"] == 12

    empty = tool_set.tool_event("source_flights_select", {"values": ["x"]}, "result", round=1, result="", duration_ms=1)
    assert empty["rows"] == 0
    assert empty["error"] is False

    refused = tool_set.tool_event("source_flights_select", {"values": ["x"]}, "result", round=1, result="error: response too long.", duration_ms=1)
    assert refused["error"] is True
    assert refused["rows"] == 0
