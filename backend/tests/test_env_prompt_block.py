"""tracking.env_prompt_block.EnvPromptBlock — the system prompt's own env
block: only for a state that reads an avance:env source, only the keys
with ai-access other than none, values truncated to MAX_ENV_VALUE_CHARS,
and nothing at all (not even an empty block) anywhere else. The model's
memory is never part of it (see Env.memory_as_text).
"""
from __future__ import annotations

import pytest

from automaton.automaton import Action, Automaton, EnvKey, Source, State
from automaton.automaton_builder import AutomatonBuilder
from tracking.env import Env
from tracking.env_prompt_block import ENV_BLOCK_HEADER, MAX_ENV_VALUE_CHARS, EnvPromptBlock
from tracking.turn_size_estimate import estimate_turn_request

pytestmark = pytest.mark.contract

ENV_SOURCE = Source(name="env", url="avance:env", ui_label="Env", ai_definition="The automaton's variables.")
FLIGHTS = Source(name="flights", url="avance:flights.csv", ui_label="Flights", ai_definition="Flights.")
ENV_KEYS = [
    EnvKey(name="flight", ai_access="readwrite", ai_definition="The flight code."),
    EnvKey(name="customer_email", ai_access="readonly", ai_definition="The customer's email."),
    EnvKey(name="_flight_record"),
]

READING = State(key="a", ui_label="A", final=True, contextual_prompt="hi", ai_may_read_sources=("env",))
MUST_READING = State(key="a", ui_label="A", final=True, contextual_prompt="hi", ai_must_read_sources=("env", "flights"))
WRITING_ONLY = State(key="b", ui_label="B", final=True, contextual_prompt="hi", ai_may_write_sources=("env",))
OTHER = State(key="b", ui_label="B", final=True, contextual_prompt="hi", ai_may_read_sources=("flights",))


def _automaton(reading: State, other: State) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target=reading.key)
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), reading.key: reading, other.key: other},
        general_prompt="", signals=[], attachments={}, general_attachments={},
        autotracking_on_ai_message=False, sources=[ENV_SOURCE, FLIGHTS], env_keys=ENV_KEYS,
    )


def test_only_a_state_that_actually_reads_the_env_source_gets_a_block_and_it_carries_only_the_exported_keys():
    env = Env(memory={"note": "x"}, action_set={"flight": "VY3003", "_flight_record": "secret"})

    block = EnvPromptBlock.for_state(env, _automaton(READING, OTHER), READING)
    assert block is not None
    assert block.text() == f"{ENV_BLOCK_HEADER}\nflight: VY3003\ncustomer_email: "
    assert "secret" not in block.text() and "note" not in block.text()

    assert EnvPromptBlock.for_state(Env(), _automaton(MUST_READING, OTHER), MUST_READING) is not None
    assert EnvPromptBlock.for_state(env, _automaton(READING, OTHER), OTHER) is None
    assert EnvPromptBlock.for_state(env, _automaton(READING, WRITING_ONLY), WRITING_ONLY) is None


def test_a_value_beyond_the_cap_is_cut_with_a_pointer_at_the_column_reads_while_one_exactly_at_it_is_left_alone():
    automaton = _automaton(READING, OTHER)

    long_value = Env(action_set={"flight": "x" * (MAX_ENV_VALUE_CHARS + 37)})
    assert EnvPromptBlock.for_state(long_value, automaton, READING).lines()["flight"] == (
        "x" * MAX_ENV_VALUE_CHARS + "[response too long — provide more specific filters via a select_rows_* read]"
    )

    exact = Env(action_set={"flight": "x" * MAX_ENV_VALUE_CHARS})
    assert EnvPromptBlock.for_state(exact, automaton, READING).lines()["flight"] == "x" * MAX_ENV_VALUE_CHARS


def test_the_turn_size_estimate_counts_memory_and_the_blocks_own_lines_separately_and_nothing_at_all_without_a_block():
    env = Env(memory={"goal": "quit"}, action_set={"flight": "VY3003", "_flight_record": "x" * 5000})
    # memory_as_text renders memory only, never the env.
    assert env.memory_as_text() == "goal: quit"

    block = EnvPromptBlock.for_state(env, _automaton(READING, OTHER), READING)
    estimate = estimate_turn_request("prompt", None, None, env, [], env_block=block)

    kinds = {(entry.kind, entry.label) for entry in estimate.entries}
    assert ("memory", "goal") in kinds
    assert ("env", "flight") in kinds and ("env", "customer_email") in kinds
    assert not any(label == "_flight_record" for _, label in kinds)

    without_block = estimate_turn_request("prompt", None, None, Env(action_set={"flight": "x" * 5000}), [])
    assert {entry.kind for entry in without_block.entries} == {"prompt"}


def test_hello_world_declares_no_tools_and_gets_no_block():
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

    assert state.ai_source_names == ()
    assert automaton.exported_env_keys() == []
    assert EnvPromptBlock.for_state(Env(), automaton, state) is None
