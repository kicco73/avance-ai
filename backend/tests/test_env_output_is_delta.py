"""The env the model writes back is a *delta* — only new/changed keys —
and the merge side must treat it as such: keys the model omits keep
their stored value. The turn protocol must say so in the prompt (the
schema field description used to ask for "all current context keys",
contradicting its own preamble, so a large env was re-emitted on every
turn as output tokens).
"""
from __future__ import annotations

import pytest

from tracking.env import Env
from tracking.metadata_handler import MetadataHandler
from tracking.turn_protocol_using_schema import TurnProtocolUsingSchema

pytestmark = pytest.mark.contract


def test_the_protocol_asks_for_a_delta_only():
    assert "all current context keys" not in TurnProtocolUsingSchema.schema["env"]
    assert "changed" in TurnProtocolUsingSchema.schema["env"]
    assert "omit" in TurnProtocolUsingSchema.prompt_preambles["env"]


def test_omitted_keys_survive_a_delta_merge():
    env = Env(stored={"language": "it", "goal": "cut down"}, action_set={"customer_record": "row"})
    env.update(MetadataHandler.parse_raw_env("goal: quit\n"))
    assert env.stored() == {"language": "it", "goal": "quit"}
    # An empty delta is a no-op, not a wipe.
    env.update(MetadataHandler.parse_raw_env(""))
    assert env.stored() == {"language": "it", "goal": "quit"}
    # Action-set keys echoed back are still discarded.
    env.update(MetadataHandler.parse_raw_env("customer_record: forged\n"))
    assert env.action_set() == {"customer_record": "row"}
