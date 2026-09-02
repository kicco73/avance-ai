"""ChatService.prepare_user_initiated_turn — WhatsApp's own turns (invite
welcome excluded): the project bootstrap still runs, but no AI-initiated
opening message is generated ahead of the user's own text unless the
current state can't take a real turn at all (final, or chat=False),
in which case that's the only thing this session would ever say.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, State
from chat.chat_service import ChatService
from tracking.fixed_project_context import FixedProjectContext
from tracking.env import PersistedEnv
from chat.session_manager import ChatSessionManager
from conftest import FakeAiService
from conftest import NullBroadcaster, make_test_actuator_factory
from jobs import JobQueue
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService

pytestmark = pytest.mark.regression

PROJECT_NAME = "proj"


def _automaton(*, final: bool, env: dict | None = None) -> Automaton:
    init_action = Action(name="init-action", ui_label="init-action", ui_button="", target="a", env=env)
    actions = [] if final else [Action(name="go", ui_label="go", ui_button="", target="a")]
    state_a = State(key="a", ui_label="A", final=final, contextual_prompt="hi", actions=actions)
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

    def get_automaton_and_state(self, project_name: str, type: str = 'live', username: str | None = None):
        return self._automaton, self._automaton.states["a"]

    def get_automaton_and_state_for_session(self, session_id: int):
        return self._automaton, self._automaton.states["a"]

    def get_active_project_name(self) -> str:
        return PROJECT_NAME

    def get_published_revision(self, project_name: str) -> int:
        return 0

    def legal_terms_pending(self, username: str, project_name: str) -> bool:
        return False

    def get_project_availability(self, project_name: str):
        return (False, None)


def _chat_service(db, automaton: Automaton) -> ChatService:
    db.ensure_project(PROJECT_NAME)
    db.publish_project(PROJECT_NAME)
    ai_service = FakeAiService()
    project_service = FakeProjectService(automaton)
    metric_service = MetricService(db, project_service)
    job_queue = JobQueue(max_concurrent=1, broadcaster=NullBroadcaster())
    actuator_factory = make_test_actuator_factory(db, job_queue)
    tracking_service = TrackingService(db, project_service, metric_service, actuator_factory)
    return ChatService(
        ai_service=ai_service,
        ai_test_service=ai_service,
        project_service=project_service,
        db=db,
        session_manager=ChatSessionManager(db),
        tracking_service=tracking_service,
        metric_service=metric_service,
        job_queue=job_queue,
        actuator_factory=actuator_factory,
    )


async def test_skips_the_opening_message_for_a_chat_enabled_state(db):
    chat_service = _chat_service(db, _automaton(final=False))
    session = await chat_service.get_current_session_if_any_or_create_new(None)

    await chat_service.prepare_user_initiated_turn(session["id"])

    assert db.get_messages(session["id"]) == []


async def test_still_generates_the_wrap_up_message_for_a_chat_blocked_state(db):
    chat_service = _chat_service(db, _automaton(final=True))
    session = await chat_service.get_current_session_if_any_or_create_new(None)

    await chat_service.prepare_user_initiated_turn(session["id"])

    assert db.get_messages(session["id"]) != []


async def test_still_applies_declared_env_defaults(db):
    chat_service = _chat_service(db, _automaton(final=False, env={"a": "2"}))
    session = await chat_service.get_current_session_if_any_or_create_new(None)

    await chat_service.prepare_user_initiated_turn(session["id"])

    env = PersistedEnv(db, FixedProjectContext(project_name=PROJECT_NAME))
    assert env.action_set() == {"a": 2}
