"""ChatService.apply_manual_action's end of the action-level `env`
feature — a manually fired (button click) action updates env exactly
like an auto-tracking-fired one does, without any signal_values.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, EnvKey, Source, State
from chat.chat_service import ChatService
from tracking.fixed_project_context import FixedProjectContext
from tracking.env import PersistedEnv
from chat.session_manager import ChatSessionManager
from conftest import FakeAiService
from conftest import make_test_actuator_factory, make_test_job_service
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService

# Each test verifies one fact about action-level env: persisted,
# self-referencing, ordered before the next prompt.
pytestmark = pytest.mark.regression

PROJECT_ID = "proj"


def _automaton(action_env: dict, target: str = "b", model_reads_env: bool = False) -> Automaton:
    """`model_reads_env`: exports every written key read-only and has the
    destination state read an avance:env source — the one configuration
    under which an env value ever reaches the model's prompt (see
    tracking.env_prompt_block); an unexported key never does."""
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target=target, env=action_env)
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    state_b = State(
        key="b", ui_label="B", final=target == "b", contextual_prompt="bye", actions=[],
        ai_may_read_sources=("env",) if model_reads_env else (),
    )
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="",
        signals=[],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=False,
        env_keys=[
            EnvKey(name=key, ai_access="readonly" if model_reads_env else "none", ai_definition=f"The {key}.")
            for key in (action_env or {})
        ],
        sources=[Source(name="env", url="avance:env", ui_label="Env", ai_definition="The variables.")] if model_reads_env else [],
    )


class FakeProjectService:
    def __init__(self, automaton: Automaton, state_key: str = "a") -> None:
        self._automaton = automaton
        self._state_key = state_key

    def get_active_automaton_and_state(self, username: str | None = None):
        return self._automaton, self._automaton.states[self._state_key]

    def get_automaton_and_state(self, project_id: str, type: str = 'live', username: str | None = None):
        return self._automaton, self._automaton.states[self._state_key]

    def get_automaton_and_state_for_session(self, session_id: int):
        return self._automaton, self._automaton.states[self._state_key]

    def get_active_project_id(self) -> str:
        return PROJECT_ID

    def get_published_revision(self, project_id: str) -> int:
        return 0

    def legal_terms_pending(self, username: str, project_id: str) -> bool:
        return False

    def get_project_availability(self, project_id: str):
        return (False, None)

    def apply_manual_action(self, action_name: str, session_id: int):
        automaton, state = self.get_active_automaton_and_state()
        action = automaton.move(state.key, action_name)
        new_state = automaton.get_state(action.target)
        self._state_key = new_state.key
        return automaton.get_state_payload(new_state), action, state.key


def _chat_service(db, automaton: Automaton) -> ChatService:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    ai_service = FakeAiService()
    project_service = FakeProjectService(automaton)
    metric_service = MetricService(db, project_service)
    job_service = make_test_job_service(db)
    actuator_factory = make_test_actuator_factory(db, job_service)
    tracking_service = TrackingService(
        db, project_service, metric_service, actuator_factory,
    )
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


def _env_for(db, session_id: int | None = None) -> PersistedEnv:
    return PersistedEnv(db, FixedProjectContext(project_id=PROJECT_ID), session_id)


async def test_a_manually_fired_actions_env_is_persisted(db):
    chat_service = _chat_service(db, _automaton({"reset_counter": "True"}))
    session = await chat_service.get_current_session_if_any_or_create_new(None)

    await chat_service.apply_manual_action("advance", session["id"])

    env = _env_for(db)
    assert env.action_set() == {"reset_counter": True}
    assert env.memory() == {}


async def test_an_action_with_no_env_field_never_touches_env(db):
    chat_service = _chat_service(db, _automaton(None))
    session = await chat_service.get_current_session_if_any_or_create_new(None)

    await chat_service.apply_manual_action("advance", session["id"])

    env = _env_for(db)
    assert env.memory() == {}
    assert env.action_set() == {}


async def test_manual_actions_env_can_self_reference_a_previously_stored_value(db):
    chat_service = _chat_service(db, _automaton({"number_of_steps": "env.number_of_steps + 1"}, target="a"))
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    env = _env_for(db, session["id"])
    env.update_action_set({"number_of_steps": 3})

    await chat_service.apply_manual_action("advance", session["id"])

    assert env.action_set()["number_of_steps"] == 4


async def test_env_update_happens_before_the_transitions_own_prompt_is_built(db):
    """The destination state's own opening-message prompt must already
    see the updated env value, not last turn's — in its env block, which
    that state gets because it reads the avance:env source."""
    chat_service = _chat_service(db, _automaton({"reset_counter": "True"}, model_reads_env=True))
    ai_service = chat_service._ai_service
    session = await chat_service.get_current_session_if_any_or_create_new(None)

    await chat_service.apply_manual_action("advance", session["id"])

    system_prompt, _ = ai_service.calls[0]
    assert "reset_counter: True" in system_prompt.full_text()


async def test_an_unexported_env_key_never_reaches_the_prompt(db):
    """ai-access: none (the default) — the automaton's env stays out of
    the model's prompt entirely, whatever the destination state declares."""
    chat_service = _chat_service(db, _automaton({"reset_counter": "True"}))
    ai_service = chat_service._ai_service
    session = await chat_service.get_current_session_if_any_or_create_new(None)

    await chat_service.apply_manual_action("advance", session["id"])

    system_prompt, _ = ai_service.calls[0]
    assert "reset_counter" not in system_prompt.full_text()
