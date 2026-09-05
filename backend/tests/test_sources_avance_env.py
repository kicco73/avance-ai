"""tracking.sources.avance_env.AvanceEnvSource — the `avance:env` driver:
the project's declared env keys with `ai-access` other than none, exposed
to the model as a one-row table. `select()` reads them (header + row,
optionally projected onto `keys`); `update(fields=...)` writes the
readwrite ones through Env.update_action_set — PersistedEnv for a live
session (a Tracking row, later bound to the turn's assistant message),
the ephemeral in-memory Env for a test session — and refuses a readonly
or unexported key as error text, writing nothing. Scripts are never
subject to ai-access; the driver is the model's channel.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, EnvKey, Source, State
from db.models import Tracking
from tracking.env import Env, PersistedEnv
from tracking.fixed_project_context import FixedProjectContext
from tracking.sources import SourceNamespace, driver_class_for
from tracking.sources.avance_archive import AvanceArchiveSource
from tracking.sources.avance_env import AvanceEnvSource
from tracking.sources.base import SourceContext

pytestmark = pytest.mark.contract

PROJECT_ID = "proj"
USERNAME = "user"

ENV_SOURCE = Source(name="env", url="avance:env", ui_label="Env", ai_definition="The automaton's variables.")
ENV_KEYS = [
    EnvKey(name="flight", ai_access="readwrite", ai_definition="The flight code."),
    EnvKey(name="pnr", ai_access="readwrite", ai_definition="The record locator."),
    EnvKey(name="customer_email", ai_access="readonly", ai_definition="The customer's email."),
    EnvKey(name="_flight_record"),
]


def _automaton(env_keys: list[EnvKey] = ENV_KEYS) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action])},
        general_prompt="", signals=[], attachments={}, general_attachments={},
        autotracking_on_ai_message=False, project_id=PROJECT_ID, sources=[ENV_SOURCE], env_keys=env_keys,
    )


def _driver(env: Env, automaton: Automaton | None = None) -> AvanceEnvSource:
    context = SourceContext(db=None, automaton=automaton or _automaton(), session_id=None, env=env)
    return AvanceEnvSource(context, "env", "env")


def _live_session(db) -> int:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    return db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID, revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1), start_state="a", end_state="a",
    )


def test_driver_class_for_picks_the_env_driver_for_avance_env_and_the_archive_one_for_any_other_avance_path():
    assert driver_class_for("avance:env") is AvanceEnvSource
    assert driver_class_for("avance:behaviour/env.csv") is AvanceArchiveSource
    assert driver_class_for("avance:sources/flights.csv") is AvanceArchiveSource


def test_source_namespace_resolves_an_env_source_to_the_env_driver(db):
    resolved = SourceNamespace(db, _automaton(), env=Env()).env
    assert isinstance(resolved, AvanceEnvSource)


class TestSelect:
    def test_returns_the_header_of_exported_keys_and_one_row_of_current_values(self):
        env = Env(action_set={"flight": "VY3003", "customer_email": "a@b.c", "_flight_record": "secret"})

        assert _driver(env).select() == "flight,pnr,customer_email\nVY3003,,a@b.c\n"

    def test_keys_project_onto_the_requested_columns_in_that_order(self):
        env = Env(action_set={"flight": "VY3003", "pnr": "ABC123"})

        assert _driver(env).select(keys=["pnr", "flight"]) == "pnr,flight\nABC123,VY3003\n"

    def test_values_are_ignored_there_is_only_one_row(self):
        env = Env(action_set={"flight": "VY3003"})

        assert _driver(env).select("nothing-like-this") == _driver(env).select()

    def test_an_unexported_or_unknown_key_is_reported_as_text(self):
        env = Env(action_set={"_flight_record": "secret"})

        result = _driver(env).select(keys=["_flight_record"])

        assert result.startswith("error: unknown variable(s) '_flight_record'")
        assert "secret" not in result

    def test_a_value_with_a_comma_is_quoted_so_the_row_stays_one_row(self):
        env = Env(action_set={"flight": "VY3003, VY3004"})

        assert _driver(env).select(keys=["flight"]) == 'flight\n"VY3003, VY3004"\n'


class TestUpdate:
    def test_writes_a_readwrite_key_into_an_in_memory_env_and_reports_one_row(self):
        env = Env(action_set={"flight": "VY1"})

        result = _driver(env).update(fields={"pnr": "ABC123", "flight": "VY3003"})

        assert result == "1 row updated"
        assert env.action_set() == {"flight": "VY3003", "pnr": "ABC123"}

    def test_a_readonly_key_is_refused_as_text_and_nothing_is_written(self):
        env = Env(action_set={"customer_email": "a@b.c"})

        result = _driver(env).update(fields={"customer_email": "x@y.z", "pnr": "ABC123"})

        assert result.startswith("error: 'customer_email' is read-only")
        assert env.action_set() == {"customer_email": "a@b.c"}

    def test_an_unexported_key_is_refused_as_text_and_nothing_is_written(self):
        env = Env()

        result = _driver(env).update(fields={"_flight_record": "x"})

        assert "'_flight_record' is not a variable you can access" in result
        assert env.action_set() == {}

    def test_an_empty_fields_map_is_refused_as_text(self):
        env = Env()

        assert _driver(env).update(fields={}).startswith("error:")

    def test_a_live_session_write_lands_in_a_tracking_row_with_origin_tool_and_no_message_yet(self, db):
        session_id = _live_session(db)
        env = PersistedEnv(db, FixedProjectContext(project_id=PROJECT_ID), session_id)

        assert _driver(env).update(fields={"pnr": "ABC123"}) == "1 row updated"

        row = Tracking.get(Tracking.action_env.is_null(False))
        assert row.origin == "tool" and row.message_id is None
        assert db.get_action_env(PROJECT_ID, USERNAME) == {"pnr": "ABC123"}

    def test_link_tool_env_writes_to_message_binds_the_turn_s_writes_to_the_assistant_message(self, db):
        session_id = _live_session(db)
        env = PersistedEnv(db, FixedProjectContext(project_id=PROJECT_ID), session_id)
        env.update_action_set({"flight": "VY1"})  # an action's own env: write — never a tool write
        _driver(env).update(fields={"pnr": "ABC123"})
        assistant_id = db.save_message("assistant", "Noted.", session_id)

        db.link_tool_env_writes_to_message(session_id, assistant_id)

        rows = list(Tracking.select().where(Tracking.action_env.is_null(False)).order_by(Tracking.id))
        assert [row.message_id for row in rows] == [None, assistant_id]
        # The bookkeeping row never masquerades as the message's evaluation point.
        assert db.get_signal_row_by_message(assistant_id) is None

    def test_a_test_session_s_ephemeral_env_takes_the_write_with_no_tracking_row(self, db):
        env = Env()

        _driver(env).update(fields={"pnr": "ABC123"})

        assert env.action_set() == {"pnr": "ABC123"}
        assert Tracking.select().count() == 0


class TestParameterSchema:
    def test_select_narrows_keys_to_an_enum_of_the_exported_keys(self):
        schema = _driver(Env()).parameter_schema("select")

        assert schema["properties"]["keys"]["items"]["enum"] == ["flight", "pnr", "customer_email"]
        assert schema["required"] == ["values"]

    def test_update_lists_only_the_readwrite_keys_each_described_by_its_ai_definition(self):
        schema = _driver(Env()).parameter_schema("update")

        fields = schema["properties"]["fields"]
        assert fields["properties"] == {
            "flight": {"type": "string", "description": "The flight code."},
            "pnr": {"type": "string", "description": "The record locator."},
        }
        assert fields["additionalProperties"] is False
        assert fields["minProperties"] == 1
        assert "values" in schema["properties"]

    def test_an_unknown_method_narrows_nothing(self):
        assert _driver(Env()).parameter_schema("nope") is None
