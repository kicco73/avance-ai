"""tracking.env_prompt_block.EnvPromptBlock — the system prompt's own env
block: shown to a state whenever it declares at least one `input` name,
carrying only those keys, values truncated to MAX_ENV_VALUE_CHARS, and
nothing at all (not even an empty block) for a state with no `input`.
The model's memory is never part of it (see Env.memory_as_text).
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, EnvKey, State
from automaton.automaton_builder import AutomatonBuilder
from tracking.env import Env
from tracking.env_prompt_block import ENV_BLOCK_HEADER, MAX_ENV_VALUE_CHARS, EnvPromptBlock
from tracking.turn_size_estimate import estimate_turn_request

pytestmark = pytest.mark.contract

ENV_KEYS = [
    EnvKey(name="flight", ai_definition="The flight code."),
    EnvKey(name="customer_email", ai_definition="The customer's email."),
    EnvKey(name="_flight_record"),
]

STATE_A = State(key="a", ui_label="A", final=True, contextual_prompt="hi", input=("flight", "customer_email"))
STATE_B = State(key="b", ui_label="B", final=True, contextual_prompt="hi", input=("flight", "customer_email"))
STATE_C = State(key="c", ui_label="C", final=True, contextual_prompt="hi")


def _automaton(*states: State) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target=states[0].key)
    all_states = {"": State(key="", ui_label="", final=False, actions=[init_action])}
    all_states.update({state.key: state for state in states})
    return Automaton(
        init_action=init_action, states=all_states,
        general_prompt="", signals=[], attachments={}, general_attachments={},
        autotracking_on_ai_message=False, sources=[], env_keys=ENV_KEYS,
    )


def test_a_state_declaring_input_gets_a_block_carrying_only_those_keys():
    env = Env(memory={"note": "x"}, action_set={"flight": "VY3003", "_flight_record": "secret"})
    automaton = _automaton(STATE_A, STATE_B, STATE_C)

    block = EnvPromptBlock.for_state(env, automaton, STATE_A)
    assert block is not None
    assert block.text() == f"{ENV_BLOCK_HEADER}\nflight: VY3003\ncustomer_email: "
    assert "secret" not in block.text() and "note" not in block.text()

    # Every state with the same `input` sees the same block.
    assert EnvPromptBlock.for_state(env, automaton, STATE_B) is not None


def test_a_state_with_no_input_gets_no_block_at_all():
    env = Env(action_set={"flight": "VY3003"})
    automaton = _automaton(STATE_A, STATE_C)

    assert EnvPromptBlock.for_state(env, automaton, STATE_C) is None


def test_a_value_beyond_the_cap_is_cut_with_a_pointer_at_the_column_reads_while_one_exactly_at_it_is_left_alone():
    automaton = _automaton(STATE_A)

    long_value = Env(action_set={"flight": "x" * (MAX_ENV_VALUE_CHARS + 37)})
    assert EnvPromptBlock.for_state(long_value, automaton, STATE_A).lines()["flight"] == (
        "x" * MAX_ENV_VALUE_CHARS + "[response too long — provide more specific filters via a select_rows_* read]"
    )

    exact = Env(action_set={"flight": "x" * MAX_ENV_VALUE_CHARS})
    assert EnvPromptBlock.for_state(exact, automaton, STATE_A).lines()["flight"] == "x" * MAX_ENV_VALUE_CHARS


def test_the_turn_size_estimate_counts_memory_and_the_blocks_own_lines_separately_and_nothing_at_all_without_a_block():
    env = Env(memory={"goal": "quit"}, action_set={"flight": "VY3003", "_flight_record": "x" * 5000})
    # memory_as_text renders memory only, never the env.
    assert env.memory_as_text() == "goal: quit"

    block = EnvPromptBlock.for_state(env, _automaton(STATE_A), STATE_A)
    estimate = estimate_turn_request("prompt", None, None, env, [], env_block=block)

    kinds = {(entry.kind, entry.label) for entry in estimate.entries}
    assert ("memory", "goal") in kinds
    assert ("env", "flight") in kinds and ("env", "customer_email") in kinds
    assert not any(label == "_flight_record" for _, label in kinds)

    without_block = estimate_turn_request("prompt", None, None, Env(action_set={"flight": "x" * 5000}), [])
    assert {entry.kind for entry in without_block.entries} == {"prompt"}


def test_hello_world_declares_no_input_and_gets_no_block():
    automaton = AutomatonBuilder().build({"index.yml": """
project:
  id: hello
init-action:
  target: Hello
states:
  Hello:
    contextual-prompt: |
      Ignore all user input. You always respond "hello, world!".
"""})
    state = automaton.states["Hello"]

    assert state.input == ()
    assert EnvPromptBlock.for_state(Env(), automaton, state) is None
