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


def _automaton(reading: State, other: State) -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target=reading.key)
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), reading.key: reading, other.key: other},
        general_prompt="", signals=[], attachments={}, general_attachments={},
        autotracking_on_ai_message=False, sources=[ENV_SOURCE, FLIGHTS], env_keys=ENV_KEYS,
    )


READING = State(key="a", ui_label="A", final=True, contextual_prompt="hi", ai_may_read_sources=("env",))
MUST_READING = State(key="a", ui_label="A", final=True, contextual_prompt="hi", ai_must_read_sources=("env", "flights"))
WRITING_ONLY = State(key="b", ui_label="B", final=True, contextual_prompt="hi", ai_may_write_sources=("env",))
OTHER = State(key="b", ui_label="B", final=True, contextual_prompt="hi", ai_may_read_sources=("flights",))


def test_a_state_reading_the_env_source_gets_a_block_with_only_the_exported_keys():
    env = Env(memory={"note": "x"}, action_set={"flight": "VY3003", "_flight_record": "secret"})
    automaton = _automaton(READING, OTHER)

    block = EnvPromptBlock.for_state(env, automaton, READING)

    assert block is not None
    assert block.text() == f"{ENV_BLOCK_HEADER}\nflight: VY3003\ncustomer_email: "
    assert "secret" not in block.text() and "note" not in block.text()


def test_a_must_read_of_the_env_source_gets_the_block_too():
    automaton = _automaton(MUST_READING, OTHER)

    assert EnvPromptBlock.for_state(Env(), automaton, MUST_READING) is not None


def test_a_state_not_reading_the_env_source_gets_no_block_at_all():
    env = Env(action_set={"flight": "VY3003"})
    automaton = _automaton(READING, OTHER)

    assert EnvPromptBlock.for_state(env, automaton, OTHER) is None


def test_a_state_only_writing_the_env_source_gets_no_block_either():
    automaton = _automaton(READING, WRITING_ONLY)

    assert EnvPromptBlock.for_state(Env(action_set={"flight": "VY3003"}), automaton, WRITING_ONLY) is None


def test_a_value_beyond_the_cap_is_cut_with_a_pointer_at_select():
    long_value = "x" * (MAX_ENV_VALUE_CHARS + 37)
    env = Env(action_set={"flight": long_value})
    automaton = _automaton(READING, OTHER)

    lines = EnvPromptBlock.for_state(env, automaton, READING).lines()

    assert lines["flight"] == (
        "x" * MAX_ENV_VALUE_CHARS + "[response too long — provide more specific filters via select]"
    )


def test_a_value_exactly_at_the_cap_is_left_alone():
    env = Env(action_set={"flight": "x" * MAX_ENV_VALUE_CHARS})
    automaton = _automaton(READING, OTHER)

    assert EnvPromptBlock.for_state(env, automaton, READING).lines()["flight"] == "x" * MAX_ENV_VALUE_CHARS


def test_memory_as_text_renders_memory_only_never_the_env():
    env = Env(memory={"goal": "quit"}, action_set={"flight": "VY3003"})

    assert env.memory_as_text() == "goal: quit"


def test_the_turn_size_estimate_counts_memory_and_the_block_s_own_lines_separately():
    env = Env(memory={"goal": "quit"}, action_set={"flight": "VY3003", "_flight_record": "x" * 5000})
    automaton = _automaton(READING, OTHER)
    block = EnvPromptBlock.for_state(env, automaton, READING)

    estimate = estimate_turn_request("prompt", None, None, env, [], env_block=block)

    kinds = {(entry.kind, entry.label) for entry in estimate.entries}
    assert ("memory", "goal") in kinds
    assert ("env", "flight") in kinds and ("env", "customer_email") in kinds
    assert not any(label == "_flight_record" for _, label in kinds)


def test_with_no_block_the_estimate_counts_nothing_for_the_env():
    env = Env(action_set={"flight": "x" * 5000})

    estimate = estimate_turn_request("prompt", None, None, env, [])

    assert {entry.kind for entry in estimate.entries} == {"prompt"}


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
