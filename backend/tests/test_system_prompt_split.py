"""TurnProtocolUsingSchema.generate_reply's own SystemPrompt split (see
ai.llm_provider.SystemPrompt): `stable` depends only on state/automaton —
every channel's own preamble/content (MemoryChannel's data header
excepted), plus SCHEMA_ORDER_PROMPT's field-order instructions — and must
be byte-identical across two turns in the same state; `volatile` is
whatever depends on the session/turn instead — MemoryChannel's own
"Current memory:" header/content, and the env block — and must change
when either does. No wording changes versus the old single concatenated
prompt: stable + volatile carries the exact same blocks, only reordered.
"""
from __future__ import annotations

import pytest

from ai import SystemPrompt
from tracking.channels import AudioChannel, MemoryChannel, SignalsChannel, TextChannel
from tracking.turn_protocol_using_schema import SCHEMA_ORDER_PROMPT, TurnProtocolUsingSchema

pytestmark = pytest.mark.contract

BASE_PROMPT = "You are a helpful assistant."
SIGNAL_DEFINITION = "- Definition of signals:\n\t- Signal \"mood\":\nmood definition"
HISTORY = [{"role": "user", "content": "hi"}]


class _StubEnv:
    def __init__(self, memory_text: str) -> None:
        self._memory_text = memory_text

    def memory_as_text(self) -> str:
        return self._memory_text


class _CapturingAiService:
    def __init__(self) -> None:
        self.captured = None

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema):
        self.captured = system_prompt
        return
        yield  # pragma: no cover - never reached, makes this an async generator


def _channels(memory_text: str) -> list:
    return [
        SignalsChannel(SIGNAL_DEFINITION), AudioChannel(), TextChannel(BASE_PROMPT), MemoryChannel(_StubEnv(memory_text)),
    ]


async def _generate(memory_text: str, env_block: str | None = None):
    ai_service = _CapturingAiService()
    protocol = TurnProtocolUsingSchema(ai_service)
    async for _ in protocol.generate_reply(_channels(memory_text), HISTORY, lambda k, v: None, env_block=env_block):
        pass
    return ai_service.captured


async def test_stable_is_identical_across_two_turns_with_different_memory():
    first = await _generate("goal: quit")
    second = await _generate("goal: continue")

    assert first.stable == second.stable
    assert first.volatile != second.volatile


async def test_memory_content_and_header_land_in_volatile_not_stable():
    prompt = await _generate("goal: quit")

    assert "Current memory:" in prompt.volatile and "goal: quit" in prompt.volatile
    assert "Current memory:" not in prompt.stable and "goal: quit" not in prompt.stable


async def test_state_dependent_content_lands_in_stable():
    prompt = await _generate("goal: quit")

    assert "mood definition" in prompt.stable
    assert BASE_PROMPT in prompt.stable
    assert SCHEMA_ORDER_PROMPT in prompt.stable


async def test_env_block_lands_in_volatile_and_changes_it_alone():
    first = await _generate("goal: quit", env_block="Current environment — the automaton's own variables.\nflight: VY3003")
    second = await _generate("goal: quit", env_block="Current environment — the automaton's own variables.\nflight: VY9999")

    assert first.stable == second.stable
    assert "flight: VY3003" in first.volatile
    assert "flight: VY9999" in second.volatile
    assert "flight:" not in first.stable and "flight:" not in second.stable


async def test_full_text_carries_every_block_the_old_single_prompt_did():
    prompt = await _generate("goal: quit", env_block="Current environment — variables.\nflight: VY3003")
    full = prompt.full_text()

    for expected in (
        "mood definition", BASE_PROMPT, SCHEMA_ORDER_PROMPT, "Current memory:", "goal: quit",
        "Current environment", "flight: VY3003",
    ):
        assert expected in full


async def test_a_plain_str_system_prompt_is_never_produced_by_generate_reply():
    """generate_reply always hands AiService a SystemPrompt, never a bare
    str — a plain str stays only for callers that build one by hand (see
    SystemPrompt.coerce)."""
    prompt = await _generate("goal: quit")

    assert isinstance(prompt, SystemPrompt)


# --- SystemPrompt itself ---

def test_coerce_wraps_a_plain_str_as_an_all_stable_prompt():
    prompt = SystemPrompt.coerce("hello")
    assert prompt == SystemPrompt(stable="hello", volatile="")


def test_coerce_leaves_an_existing_system_prompt_untouched():
    original = SystemPrompt(stable="a", volatile="b")
    assert SystemPrompt.coerce(original) is original


def test_full_text_concatenates_stable_then_volatile_with_no_separator_of_its_own():
    assert SystemPrompt(stable="a", volatile="b").full_text() == "ab"
