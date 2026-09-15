"""What TrackingEngine reports about an action's env writes: every
action-set key an action's `env:` field (or its own `on-exit` script —
same env-write timing, see Automaton.eval_action_on_exit) wrote is
handed back to the caller, who is the one that says so on the way out
(see turn/outbound.py). The engine itself publishes nothing.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, State
from automaton.scope import EvaluationScope
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
        return EvaluationScope({}, automaton=automaton, state_key=state_key)


class FakeChatNamespaceRecorder(FakeChatNamespace):
    """The real FakeChatNamespace (so chat.celebrate()/chat.notify(...)
    still evaluate as genuine ChatNamespace calls), plus recording
    push_notification's own calls — apply_action_env's real target,
    normally backed by a websocket/factory neither exists here."""

    def __init__(self) -> None:
        super().__init__(project_id="p")
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


def test_apply_transition_records_the_move_and_hands_back_what_its_action_wrote():
    automaton, state, action = _automaton(action_target="b", action_env={"counter": "1"})
    engine, sink, env = _engine()

    tracking_id, written = engine.apply_transition(
        automaton, state, action, {}, session_id=1, origin='trigger', username=USERNAME, project_id=PROJECT_ID,
    )

    assert sink.transitions == [("a", "go", "b")]
    assert written == {"counter": 1}
    assert env.updates == [{"counter": 1}]
    assert tracking_id == 1


def test_apply_transition_with_no_action_writes_nothing():
    automaton, state, _action = _automaton(action_target="b")
    engine, sink, env = _engine()

    tracking_id, written = engine.apply_transition(
        automaton, state, None, {}, session_id=1, origin='trigger',
    )

    assert (tracking_id, written) == (0, {})
    assert sink.transitions == [] and env.updates == []


def test_apply_transition_requires_an_origin():
    automaton, state, action = _automaton(action_target="b")
    engine, _sink, _env = _engine()

    with pytest.raises(TypeError):
        engine.apply_transition(automaton, state, action, {}, session_id=1)


def test_apply_action_env_returns_every_key_it_wrote():
    automaton, state, action = _automaton(action_target="a", action_env={"counter": "1", "flag": "True"})
    engine, _sink, env = _engine()

    written = engine.apply_action_env(automaton, action, {}, state.key)

    assert env.updates == [{"counter": 1, "flag": True}]
    assert written == {"counter": 1, "flag": True}


def test_apply_action_env_returns_nothing_for_an_action_that_writes_nothing():
    automaton, state, action = _automaton(action_target="a")
    engine, _sink, env = _engine()

    assert engine.apply_action_env(automaton, action, {}, state.key) == {}
    assert env.updates == []


def test_apply_action_env_also_applies_and_returns_on_exit_writes():
    automaton, state, action = _automaton(action_target="a", action_on_exit="env.counter = 1")
    engine, _sink, env = _engine()

    written = engine.apply_action_env(automaton, action, {}, state.key)

    assert env.updates == [{"counter": 1}]
    assert written == {"counter": 1}


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
