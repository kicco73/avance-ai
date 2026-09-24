"""TurnService.apply_manual_action's end of an action's `on-exit` env
writes — a manually fired (button click) action updates env exactly
like an auto-tracking-fired one does, without any signal_values.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, EnvKey, State
from automaton.model import Signal
from turn.turn_service import TurnService
from tracking.fixed_project_context import FixedProjectContext
from tracking.env import PersistedEnv
from turn.sessions.session_manager import SessionManager
from conftest import FakeAiService
from conftest import make_test_namespace_factory, make_test_scheduler_service
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService
from turn_harness import FakeProjectService
pytestmark = pytest.mark.regression

PROJECT_ID = "proj"


ENV_TYPES = {"reset_counter": "bool", "number_of_steps": "number", "score": "number"}


def _automaton(
    writes: dict | None, target: str = "b", model_reads_env: bool = False, target_memory: str = "global",
    signals: list | None = None,
) -> Automaton:
    """`model_reads_env`: declares every written key as the destination
    state's own `input` — the one configuration under which an env value
    ever reaches the model's prompt (see tracking.env_prompt_block); a
    state with no `input` never sees one."""
    action = Action(
        name="advance", ui_label="Advance", ui_button="Advance", target=target,
        on_exit="\n".join(f"env.{key} = {expression}" for key, expression in (writes or {}).items()) or None,
    )
    input_names = tuple(writes or {}) if model_reads_env else ()
    state_a = State(
        input_processor="ai", key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action],
        input=input_names if target == "a" else (),
    )
    state_b = State(
        input_processor="ai", key="b", ui_label="B", final=target == "b", contextual_prompt="bye", actions=[],
        input=input_names if target == "b" else (),
        ai_memory_scope=target_memory,
    )
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="",
        signals=signals or [],
        general_attachments={},
        autotracking_on_ai_message=False,
        env_keys=[EnvKey(name=key, type=ENV_TYPES[key]) for key in (writes or {})],
    )


def _turn_service(db, automaton: Automaton) -> tuple[TurnService, FakeAiService]:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    ai_service = FakeAiService()
    project_service = FakeProjectService(automaton, db=db)
    metric_service = MetricService(db, project_service)
    scheduler_service = make_test_scheduler_service(db)
    namespace_factory = make_test_namespace_factory(db, scheduler_service)
    tracking_service = TrackingService(
        db, project_service, metric_service, namespace_factory,
    )
    return TurnService(
        ai_service=ai_service,
        ai_test_service=ai_service,
        project_service=project_service,
        db=db,
        session_manager=SessionManager(db),
        tracking_service=tracking_service,
        metric_service=metric_service,
        scheduler_service=scheduler_service,
        namespace_factory=namespace_factory,
    ), ai_service


def _env_for(db, session_id: int = 0) -> PersistedEnv:
    return PersistedEnv(db, FixedProjectContext(project_id=PROJECT_ID), session_id)


async def test_a_manually_fired_actions_env_is_persisted(db):
    turn_service, _ = _turn_service(db, _automaton({"reset_counter": "True"}))
    session = await turn_service.enter_session(PROJECT_ID, 'live')

    await turn_service.apply_manual_action("advance", session["id"])

    env = _env_for(db)
    assert env.action_set() == {"reset_counter": True}
    assert env.memory() == {}


async def test_a_manually_fired_action_lands_through_the_same_transition_as_a_triggered_one(db):
    turn_service, _ = _turn_service(db, _automaton({"reset_counter": "True"}, target_memory="local"))
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    env = _env_for(db, session["id"])
    env.update({"note": "remembered"})

    await turn_service.apply_manual_action("advance", session["id"])

    assert env.memory() == {"note": "remembered"}
    assert db.get_local_memory(session["id"]) == {}
    assert env.action_set() == {"reset_counter": True}
    landed = [row for row in db.get_signals(session["id"]) if row["new_state"] == "b"]
    assert [(row["old_state"], row["action"], row["origin"]) for row in landed] == [("a", "advance", "manual")]


async def test_a_manually_fired_action_still_reads_the_signals_measured_before_it(db):
    """Pressing a button measures nothing, but nothing has changed since
    the last turn did: the action's own expressions read the signal
    values that turn computed, instead of a None per declared signal."""
    turn_service, _ = _turn_service(db, _automaton(
        {"score": "signal.mood * 2"},
        signals=[Signal(name="mood", ui_label="Mood", definition="how it went")],
    ))
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    db.save_signal_snapshot({"mood": 0.5}, session["id"])

    await turn_service.apply_manual_action("advance", session["id"])

    assert _env_for(db).action_set() == {"score": 1.0}


async def test_an_action_with_no_env_field_never_touches_env(db):
    turn_service, _ = _turn_service(db, _automaton(None))
    session = await turn_service.enter_session(PROJECT_ID, 'live')

    await turn_service.apply_manual_action("advance", session["id"])

    env = _env_for(db)
    assert env.memory() == {}
    assert env.action_set() == {}


async def test_manual_actions_env_can_self_reference_a_previously_stored_value(db):
    turn_service, _ = _turn_service(db, _automaton({"number_of_steps": "env.number_of_steps + 1"}, target="a"))
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    env = _env_for(db, session["id"])
    env.update_action_set({"number_of_steps": 3})

    await turn_service.apply_manual_action("advance", session["id"])

    assert env.action_set()["number_of_steps"] == 4


async def test_env_update_happens_before_the_transitions_own_prompt_is_built(db):
    """The destination state's own opening-message prompt must already
    see the updated env value, not last turn's — in its env block, which
    that state gets because it reads the avance:env source."""
    turn_service, ai_service = _turn_service(db, _automaton({"reset_counter": "True"}, model_reads_env=True))
    session = await turn_service.enter_session(PROJECT_ID, 'live')

    await turn_service.apply_manual_action("advance", session["id"])

    system_prompt, _ = ai_service.calls[0]
    assert "reset_counter: True" in system_prompt.full_text()


async def test_an_unexported_env_key_never_reaches_the_prompt(db):
    """No state declares it in its own `input` (the default) — the
    automaton's env stays out of the model's prompt entirely."""
    turn_service, ai_service = _turn_service(db, _automaton({"reset_counter": "True"}))
    session = await turn_service.enter_session(PROJECT_ID, 'live')

    await turn_service.apply_manual_action("advance", session["id"])

    system_prompt, _ = ai_service.calls[0]
    assert "reset_counter" not in system_prompt.full_text()


async def test_the_result_names_where_it_came_from_and_what_it_wrote(db):
    turn_service, _ = _turn_service(db, _automaton({"reset_counter": "True"}))
    session = await turn_service.enter_session(PROJECT_ID, 'live')

    result = await turn_service.apply_manual_action("advance", session["id"])

    assert result["from_state"] == "a"
    assert result["new_state"] == "b"
    assert result["env_changed"] == {"reset_counter": True}


async def test_an_action_that_writes_nothing_reports_an_empty_env_change(db):
    turn_service, _ = _turn_service(db, _automaton(None))
    session = await turn_service.enter_session(PROJECT_ID, 'live')

    result = await turn_service.apply_manual_action("advance", session["id"])

    assert result["env_changed"] == {}
    assert result["from_state"] == "a"
