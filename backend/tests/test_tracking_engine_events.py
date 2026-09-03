"""TrackingEngine.notify_transition/apply_action_env's own event
publishing: StateChanged for a real (non-self-loop) transition,
EnvChanged for each action-set key an action's `env:` field wrote. Both
are no-ops when username/project_id aren't given at all.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, State
from events import EnvChanged, StateChanged, subscribe
from tracking.tracking_engine import TrackingEngine

pytestmark = pytest.mark.contract

USERNAME = "user"
PROJECT_ID = "proj"


class FakeSink:
    def __init__(self):
        self.transitions = []

    def save_signal_snapshot(self, values, session_id, message_id=None):
        return 0

    def save_transition(self, old_state, action, new_state, session_id, transition_log_level, signal_values=None, message_id=None, origin=None):
        self.transitions.append((old_state, action, new_state))
        return len(self.transitions)


class FakeEnv:
    def __init__(self):
        self.updates = []

    def update_action_set(self, values):
        self.updates.append(values)


class FakeScopeBuilder:
    def build(self, automaton, state_key, signal_values, session_id=None):
        return {}


def _automaton(action_target: str, action_env: dict | None = None) -> tuple[Automaton, State, Action]:
    action = Action(name="go", ui_label="Go", ui_button="Go", target=action_target, env=action_env)
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="bye")
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    automaton = Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="",
        signals=[],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=False,
    )
    return automaton, state_a, action


def _engine() -> tuple[TrackingEngine, FakeSink, FakeEnv]:
    sink = FakeSink()
    env = FakeEnv()
    engine = TrackingEngine(sink, env, FakeScopeBuilder())
    return engine, sink, env


def _collect(event_type):
    received = []
    subscribe(event_type, received.append)
    return received


def test_apply_transition_publishes_state_changed_for_a_real_transition():
    automaton, state, action = _automaton(action_target="b")
    engine, sink, _ = _engine()
    received = _collect(StateChanged)

    engine.apply_transition(automaton, state, action, {}, session_id=1, origin='trigger', username=USERNAME, project_id=PROJECT_ID)

    assert received == [StateChanged(username=USERNAME, project_id=PROJECT_ID, from_state="a", to_state="b")]


def test_apply_transition_does_not_publish_for_a_self_loop():
    automaton, state, action = _automaton(action_target="a")
    engine, sink, _ = _engine()
    received = _collect(StateChanged)

    engine.apply_transition(automaton, state, action, {}, session_id=1, origin='trigger', username=USERNAME, project_id=PROJECT_ID)

    assert sink.transitions == [("a", "go", "a")]  # still saved — see apply_transition's own docstring
    assert received == []


def test_apply_transition_publishes_nothing_when_no_identity_is_given():
    automaton, state, action = _automaton(action_target="b")
    engine, sink, _ = _engine()
    received = _collect(StateChanged)

    engine.apply_transition(automaton, state, action, {}, session_id=1, origin='trigger')  # no username/project_id

    assert sink.transitions == [("a", "go", "b")]
    assert received == []


def test_apply_transition_requires_an_origin():
    automaton, state, action = _automaton(action_target="b")
    engine, _sink, _env = _engine()

    with pytest.raises(TypeError):
        engine.apply_transition(automaton, state, action, {}, session_id=1)


def test_apply_action_env_publishes_env_changed_per_written_key():
    automaton, state, action = _automaton(action_target="a", action_env={"counter": "1", "flag": "True"})
    engine, sink, env = _engine()
    received = _collect(EnvChanged)

    engine.apply_action_env(automaton, action, {}, state.key, username=USERNAME, project_id=PROJECT_ID)

    assert env.updates == [{"counter": 1, "flag": True}]
    assert {(e.key, e.value) for e in received} == {("counter", 1), ("flag", True)}
    assert all(e.username == USERNAME and e.project_id == PROJECT_ID for e in received)


def test_apply_action_env_publishes_nothing_when_no_identity_is_given():
    automaton, state, action = _automaton(action_target="a", action_env={"counter": "1"})
    engine, sink, env = _engine()
    received = _collect(EnvChanged)

    engine.apply_action_env(automaton, action, {}, state.key)  # no username/project_id

    assert env.updates == [{"counter": 1}]  # still applied locally
    assert received == []


def test_notify_transition_is_also_reachable_as_a_bare_staticmethod():
    """The other of its own two call sites (see the method's own
    docstring) — ProjectService.apply_manual_action calls this directly
    off the class, with no TrackingEngine instance of its own to hand."""
    received = _collect(StateChanged)

    TrackingEngine.notify_transition(USERNAME, PROJECT_ID, "a", "b")

    assert received == [StateChanged(username=USERNAME, project_id=PROJECT_ID, from_state="a", to_state="b")]
