"""A state's `ai-memory-scope` — `none` (default), `local`, `global`. What
TrackingEngine itself does on every landed transition, regardless of scope
or origin: clear the session's own local_memory cell. Which store a turn
actually renders/merges is TrackingProcessor's call, not tested here. The
automaton's env keys are never touched by any of this.
"""
from __future__ import annotations

from automaton.choice import ChoiceSelection

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
    input-processor: ai
    contextual-prompt: hi
{state_yaml}"""})


def test_the_scope_defaults_to_none_and_is_read_from_the_state():
    assert _build("").states["a"].ai_memory_scope == "none"
    assert _build("    ai-memory-scope: local\n").states["a"].ai_memory_scope == "local"


def test_a_scope_that_is_none_of_the_three_is_a_build_error():
    with pytest.raises(ValueError, match="ai-memory-scope 'wipe' must be one of \\['global', 'local', 'none'\\]"):
        _build("    ai-memory-scope: wipe\n")


class FakeSink:
    def __init__(self):
        self.transitions = []
        self.local_memory_clears = []

    def save_signal_snapshot(self, values, session_id, message_id=None, output_values=None):
        return 0

    def save_transition(self, old_state, action, new_state, session_id, transition_log_level, signal_values=None, message_id=None, origin=None, output_values=None):
        self.transitions.append((old_state, action, new_state))
        return len(self.transitions)

    def clear_local_memory(self, session_id):
        self.local_memory_clears.append(session_id)


class FakeScopeBuilder:
    def build(self, automaton, state_key, signal_values, selection, session_id=None, output_values=None):
        return {}


def _automaton(target: str, target_scope: str) -> tuple[Automaton, State, Action]:
    action = Action(name="go", ui_label="Go", ui_button="Go", target=target)
    state_a = State(
        input_processor="ai", key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action],
        ai_memory_scope=target_scope if target == "a" else "none",
    )
    state_b = State(input_processor="ai", key="b", ui_label="B", final=True, contextual_prompt="bye", ai_memory_scope=target_scope)
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    automaton = Automaton(
        init_action=init_action,
        states={"": State(input_processor="ai", key="", ui_label="", final=False, actions=[init_action]), "a": state_a, "b": state_b},
        general_prompt="", signals=[], general_attachments={}, autotracking_on_ai_message=False,
    )
    return automaton, state_a, action


def _engine() -> tuple[TrackingEngine, Env, FakeSink]:
    env = Env(memory={"note": "remembered"}, action_set={"counter": 3})
    sink = FakeSink()
    return TrackingEngine(sink, env, FakeScopeBuilder()), env, sink


@pytest.mark.parametrize("origin", ["trigger", "manual"])
@pytest.mark.parametrize("scope", ["none", "local", "global"])
def test_landing_on_any_state_clears_the_sessions_local_memory_cell_and_leaves_env(origin, scope):
    automaton, state, action = _automaton("b", scope)
    engine, env, sink = _engine()

    engine.apply_transition(automaton, state, action, None, ChoiceSelection.NONE, session_id=1, origin=origin)

    assert sink.transitions == [("a", "go", "b")]
    assert sink.local_memory_clears == [1]
    assert env.memory() == {"note": "remembered"}
    assert env.action_set() == {"counter": 3}


def test_a_self_loop_clears_the_sessions_local_memory_cell_too():
    automaton, state, action = _automaton("a", "local")
    engine, env, sink = _engine()

    engine.apply_transition(automaton, state, action, None, ChoiceSelection.NONE, session_id=1, origin="trigger")

    assert sink.local_memory_clears == [1]
    assert env.memory() == {"note": "remembered"}


def test_a_turn_that_stays_where_it_is_touches_nothing():
    automaton, state, _action = _automaton("b", "local")
    engine, env, sink = _engine()

    engine.apply_transition(automaton, state, None, {}, ChoiceSelection.NONE, session_id=1, origin="trigger")

    assert sink.local_memory_clears == []
    assert env.memory() == {"note": "remembered"}
