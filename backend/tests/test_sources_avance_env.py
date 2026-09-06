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


def _live_env(db) -> PersistedEnv:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    session_id = db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID, revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1), start_state="a", end_state="a",
    )
    return PersistedEnv(db, FixedProjectContext(project_id=PROJECT_ID), session_id)


def test_avance_env_resolves_to_the_env_driver_and_any_other_avance_path_to_the_archive_one(db):
    assert driver_class_for("avance:env") is AvanceEnvSource
    assert driver_class_for("avance:behaviour/env.csv") is AvanceArchiveSource
    assert driver_class_for("avance:sources/flights.csv") is AvanceArchiveSource
    assert isinstance(SourceNamespace(db, _automaton(), env=Env()).env, AvanceEnvSource)


def test_select_returns_the_exported_keys_header_and_one_quoted_row_projected_onto_keys_in_order_ignoring_values():
    env = Env(action_set={"flight": "VY3003", "customer_email": "a@b.c", "_flight_record": "secret"})
    assert _driver(env).select() == "flight,pnr,customer_email\nVY3003,,a@b.c\n"
    assert _driver(env).select("nothing-like-this") == _driver(env).select()

    ordered = Env(action_set={"flight": "VY3003", "pnr": "ABC123"})
    assert _driver(ordered).select(keys=["pnr", "flight"]) == "pnr,flight\nABC123,VY3003\n"

    quoted = Env(action_set={"flight": "VY3003, VY3004"})
    assert _driver(quoted).select(keys=["flight"]) == 'flight\n"VY3003, VY3004"\n'

    unexported = _driver(env).select(keys=["_flight_record"])
    assert unexported.startswith("error: unknown variable(s) '_flight_record'")
    assert "secret" not in unexported


def test_value_returns_the_current_value_the_empty_string_when_unset_and_error_text_for_an_unexported_key():
    assert _driver(Env(action_set={"flight": "VY3003"})).value(key="flight") == "VY3003"
    assert _driver(Env()).value(key="flight") == ""

    result = _driver(Env(action_set={"_flight_record": "secret"})).value(key="_flight_record")
    assert result.startswith("error: unknown variable(s) '_flight_record'")
    assert "secret" not in result


def test_update_writes_readwrite_keys_into_an_ephemeral_env_with_no_tracking_row_and_refuses_readonly_unexported_or_empty_writes(db):
    env = Env(action_set={"flight": "VY1"})
    assert _driver(env).update(fields={"pnr": "ABC123", "flight": "VY3003"}) == "1 row updated"
    assert env.action_set() == {"flight": "VY3003", "pnr": "ABC123"}
    assert Tracking.select().count() == 0

    readonly = Env(action_set={"customer_email": "a@b.c"})
    assert _driver(readonly).update(fields={"customer_email": "x@y.z", "pnr": "ABC123"}).startswith("error: 'customer_email' is read-only")
    assert readonly.action_set() == {"customer_email": "a@b.c"}

    hidden = Env()
    assert "'_flight_record' is not a variable you can access" in _driver(hidden).update(fields={"_flight_record": "x"})
    assert hidden.action_set() == {}

    assert _driver(Env()).update(fields={}).startswith("error:")


def test_a_live_session_write_lands_in_a_tracking_row_carrying_the_origin_only_when_the_tool_set_injects_it(db):
    # origin="tool" is what ToolSet.call() itself injects for a real
    # model-made call (see tracking.sources.ToolSet.call) — never the
    # driver's own default. A script/trigger calling source.env.update(...)
    # directly never claims to be the model — origin stays None,
    # indistinguishable from an action's own `env:` write.
    env = _live_env(db)

    assert _driver(env).update(fields={"pnr": "ABC123"}, origin="tool") == "1 row updated"
    tool_row = Tracking.get(Tracking.action_env.is_null(False))
    assert tool_row.origin == "tool" and tool_row.message_id is None

    assert _driver(env).update(fields={"flight": "VY1"}) == "1 row updated"
    rows = list(Tracking.select().where(Tracking.action_env.is_null(False)).order_by(Tracking.id))
    assert [row.origin for row in rows] == ["tool", None]
    assert db.get_action_env(PROJECT_ID, USERNAME) == {"pnr": "ABC123", "flight": "VY1"}


def test_link_tool_env_writes_to_message_binds_only_the_turns_tool_writes_to_the_assistant_message(db):
    env = _live_env(db)
    env.update_action_set({"flight": "VY1"})
    _driver(env).update(fields={"pnr": "ABC123"}, origin="tool")
    assistant_id = db.save_message("assistant", "Noted.", env._session_id if hasattr(env, "_session_id") else db.get_latest_chat_session(USERNAME, PROJECT_ID)["id"])

    db.link_tool_env_writes_to_message(db.get_latest_chat_session(USERNAME, PROJECT_ID)["id"], assistant_id)

    rows = list(Tracking.select().where(Tracking.action_env.is_null(False)).order_by(Tracking.id))
    assert [row.message_id for row in rows] == [None, assistant_id]
    # The bookkeeping row never masquerades as the message's evaluation point.
    assert db.get_signal_row_by_message(assistant_id) is None


def test_parameter_schemas_narrow_select_keys_to_the_exported_ones_and_update_fields_to_the_readwrite_ones():
    select = _driver(Env()).parameter_schema("select")
    assert select["properties"]["keys"]["items"]["enum"] == ["flight", "pnr", "customer_email"]
    assert select["required"] == ["values"]

    update = _driver(Env()).parameter_schema("update")
    fields = update["properties"]["fields"]
    assert fields["properties"] == {
        "flight": {"type": "string", "description": "The flight code."},
        "pnr": {"type": "string", "description": "The record locator."},
    }
    assert fields["additionalProperties"] is False
    assert fields["minProperties"] == 1
    assert "values" in update["properties"]

    assert _driver(Env()).parameter_schema("nope") is None
