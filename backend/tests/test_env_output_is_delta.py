"""The memory the model writes back is a *delta* — only new/changed keys —
and the merge side must treat it as such: keys the model omits keep
their stored value. The channel must say so in the prompt (the schema
field description used to ask for "all current context keys",
contradicting its own preamble, so a large memory was re-emitted on every
turn as output tokens).
"""
from __future__ import annotations

import pytest

from tracking.env import Env
from tracking.prompt import MemoryPrompt

pytestmark = pytest.mark.contract


def test_the_protocol_asks_for_a_delta_only():
    assert "all current context keys" not in MemoryPrompt.schema_description
    assert "changed" in MemoryPrompt.schema_description
    assert "omit" in MemoryPrompt.definition


def test_omitted_keys_survive_a_delta_merge():
    env = Env(memory={"language": "it", "goal": "cut down"}, action_set={"customer_record": "row"})
    env.update(MemoryPrompt(Env()).decode("goal: quit\n"))
    assert env.memory() == {"language": "it", "goal": "quit"}
    # An empty delta is a no-op, not a wipe.
    env.update(MemoryPrompt(Env()).decode(""))
    assert env.memory() == {"language": "it", "goal": "quit"}
    # Action-set keys echoed back are still discarded.
    env.update(MemoryPrompt(Env()).decode("customer_record: forged\n"))
    assert env.action_set() == {"customer_record": "row"}


def test_a_declared_key_not_yet_set_is_still_discarded_not_just_one_already_in_action_set():
    # declared_keys is the automaton's own declared names — the whole
    # point being it drops a key the model names *before* ever writing it
    # through `update`, not only one already sitting in action_set().
    env = Env(memory={"language": "it"})
    env.update({"pnr": "forged", "language": "en"}, declared_keys={"pnr"})
    assert env.memory() == {"language": "en"}
    assert env.action_set() == {}


def test_no_declared_keys_given_filters_nothing():
    env = Env()
    env.update({"pnr": "noted"})
    assert env.memory() == {"pnr": "noted"}
