"""TrackingEngine.notify_transition/apply_action_env's own event
publishing: StateChanged for a real (non-self-loop) transition,
EnvChanged for each action-set key an action's `env:` field (or its own
`on-exit` script — same env-write timing, see Automaton.
eval_action_on_exit) wrote. Both are no-ops when username/project_id
aren't given at all.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, State
from automaton.scope import EvaluationScope
from events import EnvChanged, StateChanged, subscribe
from tracking.actuators.chat_namespace import FakeChatNamespace
from tracking.tracking_engine import TrackingEngine

pytestmark = pytest.mark.contract

USERNAME = "user"
PROJECT_ID = "proj"


class FakeSink:
    def __init__(self):
        self.transitions = []

    def save_signal_snapshot(self, values, session_id, message_id=None, output_values=None):
        return 0

    def save_transition(self, old_state, action, new_state, session_id, transition_log_level, signal_values=None, message_id=None, origin=None, output_values=None):
        self.transitions.append((old_state, action, new_state))
        return len(self.transitions)


class FakeEnv:
    def __init__(self):
        self.updates = []

    def update_action_set(self, values):
        self.updates.append(values)


class FakeScopeBuilder:
    def build(self, automaton, state_key, signal_values, session_id=None, output_values=None):
        return {}


class FakeChatNamespaceRecorder(FakeChatNamespace):
    """The real FakeChatNamespace (so chat.celebrate()/chat.notify(...)
    still evaluate as genuine ChatNamespace calls), plus recording
    push_notification's own calls — apply_action_env's real target,
    normally backed by a websocket/factory neither exists here."""

    def __init__(self) -> None:
        super().__init__()
        self.pushed: list[str] = []

    def push_notification(self, snippet_text: str) -> None:
        self.pushed.append(snippet_text)


class FakeScopeBuilderWithChat:
    """Same as FakeScopeBuilder, but a real EvaluationScope carrying a
    fake `chat` namespace — needed once an on-exit script's own bare
    statement (a chat.* call) is evaluated through _TaskEval, which
    requires a genuine EvaluationScope, not a plain dict."""

    def __init__(self, chat: FakeChatNamespaceRecorder) -> None:
        self._chat = chat

    def build(self, automaton, state_key, signal_values, session_id=None, output_values=None):
        return EvaluationScope({"chat": self._chat}, automaton=automaton, state_key=state_key)


def _automaton(
    action_target: str, action_env: dict | None = None, action_on_exit: str | None = None,
) -> tuple[Automaton, State, Action]:
    action = Action(name="go", ui_label="Go", ui_button="Go", target=action_target, env=action_env, on_exit=action_on_exit)
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="bye")
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    automaton = Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="",
        signals=[],
        general_attachments={},
        autotracking_on_ai_message=False,
    )
    return automaton, state_a, action


def _engine() -> tuple[TrackingEngine, FakeSink, FakeEnv]:
    sink = FakeSink()
    env = FakeEnv()
    return TrackingEngine(sink, env, FakeScopeBuilder()), sink, env


def _collect(event_type):
    received = []
    subscribe(event_type, received.append)
    return received


def test_apply_transition_publishes_state_changed_only_for_a_real_transition_carrying_an_identity():
    received = _collect(StateChanged)

    real_automaton, real_state, real_action = _automaton(action_target="b")
    engine, sink, _ = _engine()
    engine.apply_transition(real_automaton, real_state, real_action, {}, session_id=1, origin='trigger', username=USERNAME, project_id=PROJECT_ID)
    assert received == [StateChanged(username=USERNAME, project_id=PROJECT_ID, from_state="a", to_state="b")]

    # A self-loop is still saved (see apply_transition's own docstring) but never published.
    loop_automaton, loop_state, loop_action = _automaton(action_target="a")
    loop_engine, loop_sink, _ = _engine()
    loop_engine.apply_transition(loop_automaton, loop_state, loop_action, {}, session_id=1, origin='trigger', username=USERNAME, project_id=PROJECT_ID)
    assert loop_sink.transitions == [("a", "go", "a")]

    anonymous_engine, anonymous_sink, _ = _engine()
    anonymous_engine.apply_transition(real_automaton, real_state, real_action, {}, session_id=1, origin='trigger')
    assert anonymous_sink.transitions == [("a", "go", "b")]

    assert len(received) == 1


def test_apply_transition_requires_an_origin():
    automaton, state, action = _automaton(action_target="b")
    engine, _sink, _env = _engine()

    with pytest.raises(TypeError):
        engine.apply_transition(automaton, state, action, {}, session_id=1)


def test_apply_action_env_publishes_env_changed_per_written_key_only_when_an_identity_is_given():
    received = _collect(EnvChanged)

    automaton, state, action = _automaton(action_target="a", action_env={"counter": "1", "flag": "True"})
    engine, _sink, env = _engine()
    engine.apply_action_env(automaton, action, {}, state.key, username=USERNAME, project_id=PROJECT_ID)

    assert env.updates == [{"counter": 1, "flag": True}]
    assert {(e.key, e.value) for e in received} == {("counter", 1), ("flag", True)}
    assert all(e.username == USERNAME and e.project_id == PROJECT_ID for e in received)

    anonymous_engine, _sink, anonymous_env = _engine()
    anonymous_engine.apply_action_env(automaton, action, {}, state.key)  # no username/project_id

    assert anonymous_env.updates == [{"counter": 1, "flag": True}]  # still applied locally
    assert len(received) == 2


def test_apply_action_env_also_applies_and_publishes_on_exit_writes():
    received = _collect(EnvChanged)

    automaton, state, action = _automaton(action_target="a", action_on_exit="env.counter = 1")
    engine, _sink, env = _engine()
    engine.apply_action_env(automaton, action, {}, state.key, username=USERNAME, project_id=PROJECT_ID)

    assert env.updates == [{"counter": 1}]
    assert {(e.key, e.value) for e in received} == {("counter", 1)}


def test_apply_action_env_prefers_on_exit_over_env_for_the_same_key():
    automaton, state, action = _automaton(
        action_target="a", action_env={"counter": "0"}, action_on_exit="env.counter = 1",
    )
    engine, _sink, env = _engine()
    engine.apply_action_env(automaton, action, {}, state.key)

    assert env.updates == [{"counter": 1}]


def test_apply_action_env_pushes_on_exits_own_chat_snippets_through_the_scopes_chat_namespace():
    """A mixed on-exit script (env write + bare chat.* call) applies its
    env update exactly as before and separately pushes the joined
    chat.* snippet text through scope["chat"].push_notification —
    synchronously, right here, never via a background ActionTask (that
    stays task's own job, see tracking/actuators/action_task.py)."""
    automaton, state, action = _automaton(
        action_target="a", action_on_exit="env.counter = 1\nchat.celebrate()\nchat.notify('Nice!', 'Done.')",
    )
    chat = FakeChatNamespaceRecorder()
    engine = TrackingEngine(FakeSink(), FakeEnv(), FakeScopeBuilderWithChat(chat))

    engine.apply_action_env(automaton, action, {}, state.key)

    assert chat.pushed == ['celebrate()\nnotify("Nice!", "Done.")']


def test_apply_action_env_never_touches_chat_when_on_exit_writes_env_only():
    """FakeScopeBuilder's own scope carries no "chat" key at all — if
    apply_action_env ever touched it for a plain env-only on-exit
    script, this would KeyError instead of passing."""
    automaton, state, action = _automaton(action_target="a", action_on_exit="env.counter = 1")
    engine, _sink, env = _engine()

    engine.apply_action_env(automaton, action, {}, state.key)

    assert env.updates == [{"counter": 1}]


def test_notify_transition_is_also_reachable_as_a_bare_staticmethod():
    """The other of its own two call sites (see the method's own
    docstring) — ProjectService.apply_manual_action calls this directly
    off the class, with no TrackingEngine instance of its own to hand."""
    received = _collect(StateChanged)

    TrackingEngine.notify_transition(USERNAME, PROJECT_ID, "a", "b")

    assert received == [StateChanged(username=USERNAME, project_id=PROJECT_ID, from_state="a", to_state="b")]
