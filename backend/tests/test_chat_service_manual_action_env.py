"""ChatService.apply_manual_action's own end of the action-level `env`
feature (see automaton_builder.py's _build_action/Automaton.
eval_action_env) — a manually fired (button click) action updates
chat.env.Env exactly like an auto-tracking-fired one does (see
test_auto_tracker_action_env.py), just without any signal_values of its
own (no AI computation runs for a manual action, see ChatService.
_apply_action_env's own docstring).
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, State
from chat.chat_service import ChatService
from tracking.env import PersistedEnv
from chat.session_manager import ChatSessionManager
from conftest import FakeAiService
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService

# Every test here verifies a specific, punctual fact about the action-
# level `env` feature (persisted, self-referencing, ordered before the
# next prompt) — still real current behavior, verified against
# chat_service.py's apply_manual_action/_apply_action_env and tracking/
# tracking_processor.py's _apply_action_env.
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

    def get_active_automaton_and_state(self):
        return self._automaton, self._automaton.states[self._state_key]

    def get_active_project_name(self) -> str:
        return PROJECT_NAME

    def apply_manual_action(self, action_name: str, session_id: int):
        automaton, state = self.get_active_automaton_and_state()
        action = automaton.move(state.key, action_name)
        new_state = automaton.get_state(action.target)
        self._state_key = new_state.key
        return automaton.get_state_payload(new_state), action, state.key


def _chat_service(db, automaton: Automaton) -> ChatService:
    ai_service = FakeAiService()
    project_service = FakeProjectService(automaton)
    metric_service = MetricService(
        db, get_username=lambda: "user", get_active_project_name=lambda: PROJECT_NAME,
    )
    # TrackingService.__init__ now takes project_service directly, not
    # get_active_automaton/get_username/get_active_project_name callables
    # (see tracking/tracking_service.py).
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
    )


def _env_for(db) -> PersistedEnv:
    return PersistedEnv(db, get_username=lambda: "user", get_active_project_name=lambda: PROJECT_NAME)


async def test_a_manually_fired_actions_env_is_persisted(db):
    chat_service = _chat_service(db, _automaton({"reset_counter": "True"}))
    session = chat_service.get_or_create_current_session(None)

    await chat_service.apply_manual_action("advance", session["id"])

    env = _env_for(db)
    # The Inspector Env tab's own "SET" section, not "AI" — see
    # Env.action_set/stored.
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
    chat_service = _chat_service(db, _automaton({"number_of_steps": "number_of_steps + 1"}, target="a"))
    session = chat_service.get_or_create_current_session(None)
    env = _env_for(db)
    env.update_action_set({"number_of_steps": 3})

    await chat_service.apply_manual_action("advance", session["id"])

    assert env.action_set()["number_of_steps"] == 4


async def test_env_update_happens_before_the_transitions_own_prompt_is_built(db):
    """The whole point of the feature: "il prossimo prompt riceva il
    nuovo ENV aggiornato" — the destination state's own opening-message
    prompt (see ChatService._build_turn_prompt -> MetadataHandler.
    build_prompt, which embeds env.to_dict()) must already see the
    updated value, not last turn's."""
    chat_service = _chat_service(db, _automaton({"reset_counter": "True"}))
    ai_service = chat_service._ai_service
    session = chat_service.get_or_create_current_session(None)

    await chat_service.apply_manual_action("advance", session["id"])

    system_prompt, _ = ai_service.calls[0]
    assert "reset_counter: True" in system_prompt
