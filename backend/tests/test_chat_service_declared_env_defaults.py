"""ChatService._apply_declared_env_defaults — every project-level env
key's own declared default (folded into init_action.env by
AutomatonBuilder) gets applied once a session opens, one key at a time
in declaration order, so a later key's default can reference an
earlier key's freshly-applied value.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, State
from chat.chat_service import ChatService
from tracking.fixed_project_context import FixedProjectContext
from tracking.env import PersistedEnv
from chat.session_manager import ChatSessionManager
from conftest import FakeAiService
from conftest import make_test_actuator_factory, make_test_job_service
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService

pytestmark = pytest.mark.regression

PROJECT_ID = "proj"


def _automaton(init_action_env: dict) -> Automaton:
    init_action = Action(name="init-action", ui_label="init-action", ui_button="", target="a", env=init_action_env)
    state_a = State(key="a", ui_label="A", final=True, contextual_prompt="hi", actions=[])
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a},
        general_prompt="",
        signals=[],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=False,
    )


class FakeProjectService:
    def __init__(self, automaton: Automaton) -> None:
        self._automaton = automaton

    def get_active_automaton_and_state(self, username: str | None = None):
        return self._automaton, self._automaton.states["a"]

    def get_automaton_and_state(self, project_id: str, type: str = 'live', username: str | None = None):
        return self._automaton, self._automaton.states["a"]

    def get_automaton_and_state_for_session(self, session_id: int):
        return self._automaton, self._automaton.states["a"]

    def get_active_project_id(self) -> str:
        return PROJECT_ID

    def get_published_revision(self, project_id: str) -> int:
        return 0

    def legal_terms_pending(self, username: str, project_id: str) -> bool:
        return False

    def get_project_availability(self, project_id: str):
        return (False, None)


def _chat_service(db, automaton: Automaton) -> ChatService:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    ai_service = FakeAiService()
    project_service = FakeProjectService(automaton)
    metric_service = MetricService(db, project_service)
    job_service = make_test_job_service(db)
    actuator_factory = make_test_actuator_factory(db, job_service)
    tracking_service = TrackingService(db, project_service, metric_service, actuator_factory)
    return ChatService(
        ai_service=ai_service,
        ai_test_service=ai_service,
        project_service=project_service,
        db=db,
        session_manager=ChatSessionManager(db),
        tracking_service=tracking_service,
        metric_service=metric_service,
        job_service=job_service,
        actuator_factory=actuator_factory,
    )


def _env_for(db, session_id: int = 0) -> PersistedEnv:
    # session_id is now required (PersistedEnv(None) raises) — every
    # no-arg call below is read-only, so a placeholder id is fine.
    return PersistedEnv(db, FixedProjectContext(project_id=PROJECT_ID), session_id)


async def test_a_later_keys_default_sees_an_earlier_keys_freshly_applied_value(db):
    """Both a and b are missing on the very first open — b's own default
    references a, so this only passes if a is actually applied before
    b's expression is evaluated (the bug: a single batched eval
    evaluated every key against the same stale, pre-open snapshot)."""
    chat_service = _chat_service(db, _automaton({"a": "2", "b": "env.a + 1"}))
    session = await chat_service.get_current_session_if_any_or_create_new(None)

    await chat_service.open_if_needed(session["id"])

    assert _env_for(db).action_set() == {"a": 2, "b": 3}


async def test_a_chain_of_three_resolves_in_declaration_order(db):
    chat_service = _chat_service(
        db, _automaton({"first": "1", "second": "env.first + 1", "third": "env.second + 1"})
    )
    session = await chat_service.get_current_session_if_any_or_create_new(None)

    await chat_service.open_if_needed(session["id"])

    assert _env_for(db).action_set() == {"first": 1, "second": 2, "third": 3}


async def test_a_key_that_already_has_a_value_is_never_recomputed(db):
    chat_service = _chat_service(db, _automaton({"a": "2"}))
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    _env_for(db, session["id"]).update_action_set({"a": 99})

    await chat_service.open_if_needed(session["id"])

    assert _env_for(db).action_set() == {"a": 99}


async def test_a_key_present_only_in_memory_is_not_already_set_the_default_still_applies(db):
    # memory() and action_set() are different stores with different
    # owners (see Env's own docstring) — a same-named memory note must
    # never count as "already set" for the automaton's own env default.
    chat_service = _chat_service(db, _automaton({"a": "2"}))
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    _env_for(db, session["id"]).update({"a": "stale note"})

    await chat_service.open_if_needed(session["id"])

    assert _env_for(db).action_set() == {"a": 2}
