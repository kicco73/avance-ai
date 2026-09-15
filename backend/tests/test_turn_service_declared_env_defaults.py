"""TurnService._backfill_declared_env_keys — every declared env key's own
default (Automaton.env_defaults_action: its `value`, else its type's own
default) is applied once a session opens, one key at a time in
declaration order, so a later key's default can reference an earlier
key's freshly-applied value. TurnService._fire_init_action — the
init-action then runs as an action: its own `env:`, `on-exit`, `task`
and one transition row.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, EnvKey, State
from db.models import Tracking
from turn.turn_service import TurnService
from tracking.fixed_project_context import FixedProjectContext
from tracking.env import PersistedEnv
from turn.sessions.session_manager import SessionManager
from conftest import FakeAiService
from conftest import make_test_namespace_factory, make_test_scheduler_service
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService

pytestmark = pytest.mark.regression

PROJECT_ID = "proj"


def _automaton(
    env_keys: list[EnvKey], *, init_env: dict | None = None, init_on_exit: str | None = None,
    init_task: str | None = None, new_session_strategy: str = "resume",
) -> Automaton:
    init_action = Action(
        name="init-action", ui_label="init-action", ui_button="", target="a",
        env=init_env, on_exit=init_on_exit, task=init_task,
    )
    state_a = State(key="a", ui_label="A", final=True, contextual_prompt="hi", actions=[])
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a},
        general_prompt="",
        signals=[],
        general_attachments={},
        autotracking_on_ai_message=False,
        env_keys=env_keys,
        project_id=PROJECT_ID,
        new_session_strategy=new_session_strategy,
    )


def _number(name: str, value: str = "") -> EnvKey:
    return EnvKey(name=name, type="number", value=value)


class FakeProjectService:
    def __init__(self, automaton: Automaton) -> None:
        self._automaton = automaton

    def get_active_automaton_and_state(self, username: str | None = None):
        return self._automaton, self._automaton.states["a"]

    def get_automaton_and_state(self, project_id: str, type: str = 'live', username: str | None = None):
        return self._automaton, self._automaton.states["a"]

    def get_automaton_and_state_for_session(self, session_id: int):
        return self._automaton, self._automaton.states["a"]

    def get_automaton_for_session(self, session_id: int):
        return self._automaton

    def get_active_project_id(self) -> str:
        return PROJECT_ID

    def get_published_revision(self, project_id: str) -> int:
        return 0

    def legal_terms_pending(self, username: str, project_id: str) -> bool:
        return False

    def get_project_availability(self, project_id: str):
        return (False, None)


def _turn_service(db, automaton: Automaton) -> TurnService:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    ai_service = FakeAiService()
    project_service = FakeProjectService(automaton)
    metric_service = MetricService(db, project_service)
    scheduler_service = make_test_scheduler_service(db)
    namespace_factory = make_test_namespace_factory(db, scheduler_service)
    tracking_service = TrackingService(db, project_service, metric_service, namespace_factory)
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
    )


def _env_for(db, session_id: int = 0) -> PersistedEnv:
    return PersistedEnv(db, FixedProjectContext(project_id=PROJECT_ID), session_id)


def _init_action_rows(session_id: int) -> list[Tracking]:
    return list(Tracking.select().where((Tracking.session == session_id) & (Tracking.origin == "init-action")))


async def test_a_later_keys_default_sees_an_earlier_keys_freshly_applied_value(db):
    """Both a and b are missing on the very first open — b's own default
    references a, so this only passes if a is actually applied before
    b's expression is evaluated (the bug: a single batched eval
    evaluated every key against the same stale, pre-open snapshot)."""
    turn_service = _turn_service(db, _automaton([_number("a", "2"), _number("b", "env.a + 1")]))
    session = await turn_service.enter_session(PROJECT_ID, 'live')

    await turn_service.open_conversation(session["id"])

    assert _env_for(db).action_set() == {"a": 2, "b": 3}


async def test_a_chain_of_three_resolves_in_declaration_order(db):
    turn_service = _turn_service(
        db, _automaton([_number("first", "1"), _number("second", "env.first + 1"), _number("third", "env.second + 1")])
    )
    session = await turn_service.enter_session(PROJECT_ID, 'live')

    await turn_service.open_conversation(session["id"])

    assert _env_for(db).action_set() == {"first": 1, "second": 2, "third": 3}


async def test_a_key_without_a_value_takes_its_types_own_default(db):
    turn_service = _turn_service(db, _automaton([
        _number("count"), EnvKey(name="name", type="string"), EnvKey(name="flag", type="bool"),
        EnvKey(name="slot", type="choice"), _number("given", "7"),
    ]))
    session = await turn_service.enter_session(PROJECT_ID, 'live')

    await turn_service.open_conversation(session["id"])

    assert _env_for(db).action_set() == {"count": 0, "name": "", "flag": False, "slot": [], "given": 7}


async def test_a_key_that_already_has_a_value_is_never_recomputed(db):
    turn_service = _turn_service(db, _automaton([_number("a", "2")]))
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    _env_for(db, session["id"]).update_action_set({"a": 99})

    await turn_service.open_conversation(session["id"])

    assert _env_for(db).action_set() == {"a": 99}


async def test_a_key_present_only_in_memory_is_not_already_set_the_default_still_applies(db):
    turn_service = _turn_service(db, _automaton([_number("a", "2")]))
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    _env_for(db, session["id"]).update({"a": "stale note"})

    await turn_service.open_conversation(session["id"])

    assert _env_for(db).action_set() == {"a": 2}


async def test_the_init_actions_own_env_and_on_exit_write_even_a_key_that_is_already_set(db):
    turn_service = _turn_service(db, _automaton(
        [_number("a", "2"), _number("b", "5"), _number("c")],
        init_env={"a": "10"}, init_on_exit="env.b = 11",
    ))
    session = await turn_service.enter_session(PROJECT_ID, 'live')
    _env_for(db, session["id"]).update_action_set({"a": 99, "b": 99})

    await turn_service.open_conversation(session["id"])

    assert _env_for(db).action_set() == {"a": 10, "b": 11, "c": 0}


async def test_the_first_bootstrap_of_a_project_records_exactly_one_init_action_row(db):
    turn_service = _turn_service(db, _automaton([_number("a", "2")]))
    session = await turn_service.enter_session(PROJECT_ID, 'live')

    await turn_service.open_conversation(session["id"])
    await turn_service.open_conversation(session["id"])

    rows = _init_action_rows(session["id"])
    assert [(row.old_state, row.action, row.new_state) for row in rows] == [("", "init-action", "a")]


async def test_a_restart_session_schedules_the_init_actions_task_once_and_records_one_row(db):
    turn_service = _turn_service(db, _automaton(
        [_number("a", "2")], init_task="task.send_mail(user.email, 'hi')", new_session_strategy="restart",
    ))
    session = await turn_service.enter_session(PROJECT_ID, 'live')

    await turn_service.open_conversation(session["id"])

    assert [task["payload"]["script"].strip() for task in db.list_tasks()] == ["task.send_mail(user.email, 'hi')"]
    assert len(_init_action_rows(session["id"])) == 1
    assert _env_for(db).action_set() == {"a": 2}
