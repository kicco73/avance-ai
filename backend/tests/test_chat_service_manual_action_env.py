"""ChatService.apply_manual_action's end of the action-level `env`
feature — a manually fired (button click) action updates env exactly
like an auto-tracking-fired one does, without any signal_values.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, State
from chat.chat_service import ChatService
from tracking.fixed_project_context import FixedProjectContext
from tracking.env import PersistedEnv
from chat.session_manager import ChatSessionManager
from conftest import FakeAiService
from conftest import NullBroadcaster
from jobs import JobQueue
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService

# Each test verifies one fact about action-level env: persisted,
# self-referencing, ordered before the next prompt.
pytestmark = pytest.mark.regression

PROJECT_NAME = "proj"


def _automaton(action_env: dict, target: str = "b") -> Automaton:
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target=target, env=action_env)
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    state_b = State(key="b", ui_label="B", final=target == "b", contextual_prompt="bye", actions=[])
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="",
        signals=[],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=False,
    )


class FakeProjectService:
    def __init__(self, automaton: Automaton, state_key: str = "a") -> None:
        self._automaton = automaton
        self._state_key = state_key

    def get_active_automaton_and_state(self, username: str | None = None):
        return self._automaton, self._automaton.states[self._state_key]

    def get_automaton_and_state(self, project_name: str, type: str = 'live', username: str | None = None):
        return self._automaton, self._automaton.states[self._state_key]

    def get_automaton_and_state_for_session(self, session_id: int):
        return self._automaton, self._automaton.states[self._state_key]

    def get_active_project_name(self) -> str:
        return PROJECT_NAME

    def get_published_revision(self, project_name: str) -> int:
        return 0

    def get_project_availability(self, project_name: str):
        return (False, None)

    def apply_manual_action(self, action_name: str, session_id: int):
        automaton, state = self.get_active_automaton_and_state()
        action = automaton.move(state.key, action_name)
        new_state = automaton.get_state(action.target)
        self._state_key = new_state.key
        return automaton.get_state_payload(new_state), action, state.key


def _chat_service(db, automaton: Automaton) -> ChatService:
    db.ensure_project(PROJECT_NAME)
    db.publish_project(PROJECT_NAME)
    ai_service = FakeAiService()
    project_service = FakeProjectService(automaton)
    metric_service = MetricService(db, project_service)
    tracking_service = TrackingService(
        db, ai_service, project_service, metric_service,
    )
    return ChatService(
        ai_service=ai_service,
        project_service=project_service,
        db=db,
        session_manager=ChatSessionManager(db),
        tracking_service=tracking_service,
        metric_service=metric_service,
        job_queue=JobQueue(max_concurrent=1, broadcaster=NullBroadcaster()),
    )


def _env_for(db) -> PersistedEnv:
    return PersistedEnv(db, FixedProjectContext(project_name=PROJECT_NAME))


async def test_a_manually_fired_actions_env_is_persisted(db):
    chat_service = _chat_service(db, _automaton({"reset_counter": "True"}))
    session = chat_service.get_or_create_current_session(None)

    await chat_service.apply_manual_action("advance", session["id"])

    env = _env_for(db)
    assert env.action_set() == {"reset_counter": True}
    assert env.stored() == {}


async def test_an_action_with_no_env_field_never_touches_env(db):
    chat_service = _chat_service(db, _automaton(None))
    session = chat_service.get_or_create_current_session(None)

    await chat_service.apply_manual_action("advance", session["id"])

    env = _env_for(db)
    assert env.stored() == {}
    assert env.action_set() == {}


async def test_manual_actions_env_can_self_reference_a_previously_stored_value(db):
    chat_service = _chat_service(db, _automaton({"number_of_steps": "env.number_of_steps + 1"}, target="a"))
    session = chat_service.get_or_create_current_session(None)
    env = _env_for(db)
    env.update_action_set({"number_of_steps": 3})

    await chat_service.apply_manual_action("advance", session["id"])

    assert env.action_set()["number_of_steps"] == 4


async def test_env_update_happens_before_the_transitions_own_prompt_is_built(db):
    """The destination state's own opening-message prompt must already
    see the updated env value, not last turn's."""
    chat_service = _chat_service(db, _automaton({"reset_counter": "True"}))
    ai_service = chat_service._ai_service
    session = chat_service.get_or_create_current_session(None)

    await chat_service.apply_manual_action("advance", session["id"])

    system_prompt, _ = ai_service.calls[0]
    assert "reset_counter: True" in system_prompt
