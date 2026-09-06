"""ToolSet — the model's own callable catalog for a state's declared
`ai-may-read-sources:`/`ai-must-read-sources:`/`ai-may-write-sources:` (see
automaton.State), built by SourceNamespace.tool_set(may_read, must_read,
may_write): one `select` ToolSpec per read source, one `update` per write
source, and call() resolving through the same SourceNamespace (and so the
same per-session read cache and Env) a source.<name>.<method>() expression
already uses.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, EnvKey, Source, State
from db.db import Db
from tracking.env import Env
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


def test_specs_covers_every_named_source_and_every_supported_method(db):
    automaton = _two_sources(db)
    tool_set = SourceNamespace(db, automaton).tool_set(["flights", "tickets"])

    names = {spec.name for spec in tool_set.specs()}
    assert names == {"source_flights_select", "source_tickets_select"}


def test_value_is_never_exposed_as_a_tool_even_for_a_driver_that_supports_it(db):
    # AvanceEnvSource.SUPPORTED_METHODS includes "value" (scripts/triggers
    # only) — ToolSet must still only ever wire up select/update.
    automaton = _env_automaton(db)
    tool_set = SourceNamespace(db, automaton, env=Env()).tool_set(["env"], may_write_names=["env"])

    names = {spec.name for spec in tool_set.specs()}
    assert names == {"source_env_select", "source_env_update"}
    assert not any(name.endswith("_value") for name in names)


def test_only_the_named_sources_are_included_even_when_more_are_declared(db):
    automaton = _two_sources(db)
    tool_set = SourceNamespace(db, automaton).tool_set(["flights"])

    names = {spec.name for spec in tool_set.specs()}
    assert names == {"source_flights_select"}


def test_an_unknown_name_passed_to_tool_set_raises(db):
    automaton = _two_sources(db)

    with pytest.raises(ValueError, match="source.nope"):
        SourceNamespace(db, automaton).tool_set(["nope"])


def test_an_unknown_name_in_must_names_raises_too(db):
    automaton = _two_sources(db)

    with pytest.raises(ValueError, match="source.nope"):
        SourceNamespace(db, automaton).tool_set([], ["nope"])


def test_description_combines_the_method_blurb_and_the_source_s_own_ai_definition(db):
    automaton = _two_sources(db)
    tool_set = SourceNamespace(db, automaton).tool_set(["flights"])

    select_spec = next(spec for spec in tool_set.specs() if spec.name == "source_flights_select")
    assert "Grep over this source" in select_spec.description
    assert "Flight records." in select_spec.description


def test_ui_description_never_leaks_into_the_tool_description(db):
    """ui-description is written for the human (the Inspector/design
    view); ai-definition is written for the model. Only the latter may
    ever reach a ToolSpec — a source with both must show only its own
    ai-definition, never the ui-description text."""
    automaton = _automaton(PROJECT_ID, _seed(
        db, {"flights.csv": b"a,b\n1,2\n"}, {"flights.csv": "text/csv"},
    ), [Source(
        name="flights", url="avance:flights.csv", ui_label="Flights",
        ui_description="Shown in the Inspector only.", ai_definition="Read by the model only.",
    )])
    tool_set = SourceNamespace(db, automaton).tool_set(["flights"])

    select_spec = next(spec for spec in tool_set.specs() if spec.name == "source_flights_select")
    assert "Read by the model only." in select_spec.description
    assert "Shown in the Inspector only." not in select_spec.description


def test_description_is_just_the_method_blurb_when_the_source_has_no_ai_definition(db):
    automaton = _two_sources(db)
    tool_set = SourceNamespace(db, automaton).tool_set(["tickets"])

    select_spec = next(spec for spec in tool_set.specs() if spec.name == "source_tickets_select")
    assert select_spec.description == select_spec.description.strip()
    assert "\n\n" not in select_spec.description


def test_select_s_parameters_schema_is_the_uniform_method_schema_for_a_driver_that_narrows_nothing(db):
    automaton = _two_sources(db)
    tool_set = SourceNamespace(db, automaton).tool_set(["flights"])
    specs = {spec.name: spec for spec in tool_set.specs()}

    parameters = specs["source_flights_select"].parameters
    assert parameters is METHOD_SCHEMAS["select"]
    assert parameters["required"] == ["values"]
    assert parameters["properties"]["values"]["type"] == "array"
    assert "minItems" not in parameters["properties"]["values"]
    assert parameters["properties"]["keys"]["items"] == {"type": "string"}


def test_the_uniform_update_schema_takes_values_and_a_non_empty_string_map_of_fields():
    parameters = METHOD_SCHEMAS["update"]
    assert parameters["required"] == ["values", "fields"]
    assert parameters["properties"]["fields"]["type"] == "object"
    assert parameters["properties"]["fields"]["additionalProperties"] == {"type": "string"}
    assert parameters["properties"]["fields"]["minProperties"] == 1


def test_required_specs_is_empty_when_nothing_is_a_must_source(db):
    automaton = _two_sources(db)
    tool_set = SourceNamespace(db, automaton).tool_set(["flights", "tickets"])

    assert tool_set.required_specs() == []


def test_required_specs_covers_only_the_must_sources(db):
    automaton = _two_sources(db)
    tool_set = SourceNamespace(db, automaton).tool_set(["flights"], ["tickets"])

    assert {spec.name for spec in tool_set.specs()} == {"source_flights_select", "source_tickets_select"}
    assert {spec.name for spec in tool_set.required_specs()} == {"source_tickets_select"}


def test_a_write_source_gets_an_update_tool_and_a_read_source_a_select_tool(db):
    automaton = _env_automaton(db)
    tool_set = SourceNamespace(db, automaton, env=Env()).tool_set(["env"], [], ["env"])

    assert {spec.name for spec in tool_set.specs()} == {"source_env_select", "source_env_update"}


def test_required_specs_never_contains_an_update_even_when_the_same_source_is_a_must_read(db):
    """`must` forces a read only — a write is never forced."""
    automaton = _env_automaton(db)
    tool_set = SourceNamespace(db, automaton, env=Env()).tool_set([], ["env"], ["env"])

    assert {spec.name for spec in tool_set.required_specs()} == {"source_env_select"}


def test_a_write_on_a_source_whose_driver_has_no_update_raises(db):
    automaton = _two_sources(db)

    with pytest.raises(ValueError, match="source.flights.update"):
        SourceNamespace(db, automaton).tool_set([], [], ["flights"])


def test_a_driver_s_own_parameter_schema_narrows_the_uniform_one(db):
    automaton = _env_automaton(db)
    tool_set = SourceNamespace(db, automaton, env=Env()).tool_set(["env"], [], ["env"])
    specs = {spec.name: spec for spec in tool_set.specs()}

    select = specs["source_env_select"].parameters
    assert select["properties"]["keys"]["items"]["enum"] == ["pnr", "customer_email"]
    update = specs["source_env_update"].parameters
    assert set(update["properties"]["fields"]["properties"]) == {"pnr"}
    assert update["properties"]["fields"]["properties"]["pnr"]["description"] == "The record locator."
    assert update["properties"]["fields"]["additionalProperties"] is False
    assert update["properties"]["fields"]["minProperties"] == 1
    assert update["required"] == ["values", "fields"]


async def test_call_passes_keys_through_to_select_by_keyword(file_db):
    automaton = _automaton(PROJECT_ID, _seed(
        file_db, {"flights.csv": b"code,date,city\nVY3003,2026-06-01,Paris\n"}, {"flights.csv": "text/csv"},
    ), [Source(name="flights", url="avance:flights.csv", ui_label="Flights")])
    tool_set = SourceNamespace(file_db, automaton).tool_set(["flights"])

    result = await tool_set.call("source_flights_select", {"values": ["VY3003"], "keys": ["city", "code"]})

    assert result == "city,code\nParis,VY3003\n"


async def test_call_routes_an_update_to_the_driver_and_writes_the_env(file_db):
    env = Env()
    automaton = _env_automaton(file_db)
    tool_set = SourceNamespace(file_db, automaton, env=env).tool_set(["env"], [], ["env"])

    result = await tool_set.call("source_env_update", {"values": [], "fields": {"pnr": "ABC123"}})

    assert result == "1 row updated"
    assert env.action_set() == {"pnr": "ABC123"}


async def test_call_injects_origin_tool_for_a_write_never_from_the_models_own_arguments(file_db):
    # origin is never part of any tool's own JSON schema (the model can't
    # see or spoof it) — ToolSet.call injects it itself, in Python, only
    # for a write (see its own docstring).
    from datetime import datetime

    from db.models import Tracking
    from tracking.env import PersistedEnv
    from tracking.fixed_project_context import FixedProjectContext

    file_db.ensure_project(PROJECT_ID)
    file_db.publish_project(PROJECT_ID)
    session_id = file_db.create_chat_session(
        username="user", project_id=PROJECT_ID, revision=file_db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1), start_state="a", end_state="a",
    )
    env = PersistedEnv(file_db, FixedProjectContext(project_id=PROJECT_ID), session_id)
    automaton = _env_automaton(file_db)
    tool_set = SourceNamespace(file_db, automaton, env=env).tool_set(["env"], [], ["env"])

    await tool_set.call("source_env_update", {"values": [], "fields": {"pnr": "ABC123"}})

    row = Tracking.get(Tracking.action_env.is_null(False))
    assert row.origin == "tool"


async def test_call_resolves_to_the_named_source_s_own_driver(file_db):
    automaton = _two_sources(file_db)
    tool_set = SourceNamespace(file_db, automaton).tool_set(["flights", "tickets"])

    assert await tool_set.call("source_flights_select", {"values": ["paris"]}) == "city,country\nParis,France\n"
    assert await tool_set.call("source_tickets_select", {"values": ["12A"]}) == "id,seat\n1,12A\n"


async def test_call_with_more_than_one_value_narrows_down_the_result(file_db):
    automaton = _automaton(PROJECT_ID, _seed(
        file_db, {"flights.csv": b"code,date\nVY3003,2026-06-01\nVY3003,2026-06-02\n"}, {"flights.csv": "text/csv"},
    ), [Source(name="flights", url="avance:flights.csv", ui_label="Flights")])
    tool_set = SourceNamespace(file_db, automaton).tool_set(["flights"])

    result = await tool_set.call("source_flights_select", {"values": ["VY3003", "2026-06-01"]})

    assert result == "code,date\nVY3003,2026-06-01\n"


async def test_call_with_an_unknown_tool_name_returns_an_error_string_not_an_exception(db):
    automaton = _two_sources(db)
    tool_set = SourceNamespace(db, automaton).tool_set(["flights"])

    result = await tool_set.call("source_nope_select", {"values": ["x"]})

    assert result.startswith("error: unknown tool 'source_nope_select'.")


async def test_call_a_driver_exception_comes_back_as_an_error_string_too(file_db):
    revision = _seed(file_db, {"flights.csv": b"city,country\nParis,France\n"}, {"flights.csv": "text/csv"})
    sources = [Source(name="ghost", url="avance:missing.csv", ui_label="Ghost")]
    automaton = _automaton(PROJECT_ID, revision, sources)
    tool_set = SourceNamespace(file_db, automaton).tool_set(["ghost"])

    # ghost's own archive file was never seeded — select() raises inside
    # the driver call, which call() must turn into a string, never propagate.
    result = await tool_set.call("source_ghost_select", {"values": ["x"]})

    assert result.startswith("error:")


async def test_call_never_blocks_the_event_loop(file_db):
    """The driver call runs synchronous disk/DB I/O — asyncio.to_thread
    is what keeps it off the loop; a crude proxy for that here is just
    that awaiting it actually yields a real result without the test
    itself needing a thread of its own."""
    automaton = _two_sources(file_db)
    tool_set = SourceNamespace(file_db, automaton).tool_set(["flights"])

    result = await tool_set.call("source_flights_select", {"values": ["paris"]})

    assert result == "city,country\nParis,France\n"


