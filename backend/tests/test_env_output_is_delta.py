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
from tracking.metadata_channels import MemoryChannel

pytestmark = pytest.mark.contract


def test_the_protocol_asks_for_a_delta_only():
    assert "all current context keys" not in MemoryChannel.schema_description
    assert "changed" in MemoryChannel.schema_description
    assert "omit" in MemoryChannel.preamble


def test_omitted_keys_survive_a_delta_merge():
    env = Env(memory={"language": "it", "goal": "cut down"}, action_set={"customer_record": "row"})
    env.update(MemoryChannel(Env()).decode("goal: quit\n"))
    assert env.memory() == {"language": "it", "goal": "quit"}
    # An empty delta is a no-op, not a wipe.
    env.update(MemoryChannel(Env()).decode(""))
    assert env.memory() == {"language": "it", "goal": "quit"}
    # Action-set keys echoed back are still discarded.
    env.update(MemoryChannel(Env()).decode("customer_record: forged\n"))
    assert env.action_set() == {"customer_record": "row"}
