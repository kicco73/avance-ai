"""A state's `ai-memory-strategy` — what landing on it does to the model's
own memory: `keep` (default) leaves it, `clear` wipes it as the transition
lands, whichever flow fired the transition. The automaton's env keys are
never touched by it.
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, State
from automaton.automaton_builder import AutomatonBuilder
from tracking.env import Env
from tracking.tracking_engine import TrackingEngine

pytestmark = pytest.mark.contract


def _build(state_yaml: str) -> Automaton:
    return AutomatonBuilder().build({"index.yml": f"""
project:
  id: test_project
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
{state_yaml}"""})


def test_the_strategy_defaults_to_keep_and_is_read_from_the_state():
    assert _build("").states["a"].ai_memory_strategy == "keep"
    assert _build("    ai-memory-strategy: clear\n").states["a"].ai_memory_strategy == "clear"


def test_a_strategy_that_is_neither_keep_nor_clear_is_a_build_error():
    with pytest.raises(ValueError, match="ai-memory-strategy 'wipe' must be one of \\['clear', 'keep'\\]"):
        _build("    ai-memory-strategy: wipe\n")


class FakeSink:
    def __init__(self):
        self.transitions = []

    def save_signal_snapshot(self, values, session_id, message_id=None, output_values=None):
        return 0

    def save_transition(self, old_state, action, new_state, session_id, transition_log_level, signal_values=None, message_id=None, origin=None, output_values=None):
        self.transitions.append((old_state, action, new_state))
        return len(self.transitions)


class FakeScopeBuilder:
    def build(self, automaton, state_key, signal_values, session_id=None, output_values=None):
        return {}


def _automaton(target: str, target_strategy: str) -> tuple[Automaton, State, Action]:
    action = Action(name="go", ui_label="Go", ui_button="Go", target=target)
    state_a = State(
        key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action],
        ai_memory_strategy=target_strategy if target == "a" else "keep",
    )
    state_b = State(key="b", ui_label="B", final=True, contextual_prompt="bye", ai_memory_strategy=target_strategy)
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    automaton = Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="", signals=[], general_attachments={}, autotracking_on_ai_message=False,
    )
    return automaton, state_a, action


def _engine() -> tuple[TrackingEngine, Env, FakeSink]:
    env = Env(memory={"note": "remembered"}, action_set={"counter": 3})
    sink = FakeSink()
    return TrackingEngine(sink, env, FakeScopeBuilder()), env, sink


@pytest.mark.parametrize("origin", ["trigger", "manual"])
def test_landing_on_a_clear_state_wipes_the_memory_and_leaves_the_env_keys(origin):
    automaton, state, action = _automaton("b", "clear")
    engine, env, sink = _engine()

    engine.apply_transition(automaton, state, action, None, session_id=1, origin=origin)

    assert sink.transitions == [("a", "go", "b")]
    assert env.memory() == {}
    assert env.action_set() == {"counter": 3}


def test_landing_on_a_keep_state_leaves_the_memory():
    automaton, state, action = _automaton("b", "keep")
    engine, env, _sink = _engine()

    engine.apply_transition(automaton, state, action, None, session_id=1, origin="trigger")

    assert env.memory() == {"note": "remembered"}


def test_a_self_loop_lands_on_the_state_again_and_clears_too():
    automaton, state, action = _automaton("a", "clear")
    engine, env, _sink = _engine()

    engine.apply_transition(automaton, state, action, None, session_id=1, origin="trigger")

    assert env.memory() == {}


def test_a_turn_that_stays_where_it_is_touches_nothing():
    automaton, state, _action = _automaton("b", "clear")
    engine, env, _sink = _engine()

    engine.apply_transition(automaton, state, None, {}, session_id=1, origin="trigger")

    assert env.memory() == {"note": "remembered"}
