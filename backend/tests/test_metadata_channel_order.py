"""Pins the streaming order of the four metadata channels
(signals/audio/text/memory): "before" mode -> signals, audio, text, memory;
"after" mode -> audio, text, signals, memory. "text" is never itself an
on_metadata event — its position is the moment the first reply chunk is yielded.
"""
from __future__ import annotations

import pytest

from tracking.prompt import AudioPrompt, MemoryPrompt, Prompt, ReactionPrompt, SignalsPrompt, TextPrompt
from tracking.tracking_processor import TrackingProcessor
from tracking.turn_protocol_using_schema import TurnProtocolUsingSchema

pytestmark = pytest.mark.contract


class _StubEnv:
    def memory_as_text(self) -> str:
        return ""


BASE_PROMPT = "You are a helpful assistant."
HISTORY = [{"role": "user", "content": "hi"}]


class FakeAiServiceV2:
    """Replays a caller-supplied, ordered sequence of ("signals"/"audio"/
    "memory", value) on_metadata calls and ("text", chunk) yields, letting a
    test dictate the full four-channel interleaving directly."""

    def __init__(self, events: list[tuple[str, str]]) -> None:
        self._events = events

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema):
        for kind, value in self._events:
            if kind == "text":
                yield value
            else:
                on_metadata(kind, value)


async def _run_v2(prompt: Prompt, events: list[tuple[str, str]]) -> list[str]:
    ai_service = FakeAiServiceV2(events)
    protocol = TurnProtocolUsingSchema(ai_service)

    seen: list[str] = []
    text_seen = False

    def on_metadata(key: str, value) -> None:
        seen.append(key)

    async for chunk in protocol.generate_reply(prompt, HISTORY, on_metadata):
        if chunk.strip() and not text_seen:
            seen.append("text")
            text_seen = True

    return seen


def _first_occurrence_order(events: list[str]) -> list[str]:
    seen: list[str] = []
    for event in events:
        if event not in seen:
            seen.append(event)
    return seen


async def test_before_mode_orders_signals_audio_text_memory():
    scripted = [
        ("signals", "{}"),
        ("audio", "hi there"),
        ("text", "some visible reply text"),
        ("memory", "k: v"),
    ]
    prompt = Prompt.chain(SignalsPrompt(None), AudioPrompt(), TextPrompt(BASE_PROMPT), MemoryPrompt(_StubEnv()))
    events = await _run_v2(prompt, scripted)
    assert _first_occurrence_order(events) == ["signals", "audio", "text", "memory"]


async def test_after_mode_orders_audio_text_signals_memory():
    scripted = [
        ("audio", "hi there"),
        ("text", "some visible reply text"),
        ("signals", "{}"),
        ("memory", "k: v"),
    ]
    prompt = Prompt.chain(AudioPrompt(), TextPrompt(BASE_PROMPT), SignalsPrompt(None), MemoryPrompt(_StubEnv()))
    events = await _run_v2(prompt, scripted)
    assert _first_occurrence_order(events) == ["audio", "text", "signals", "memory"]


# --- 'reaction' channel: excluded by default, inserted right after 'signals' ---

def test_reaction_channel_excluded_by_default():
    prompt = Prompt.chain(None, None, None, TextPrompt(BASE_PROMPT), MemoryPrompt(_StubEnv()))
    assert "reaction" not in prompt.schema()


def test_reaction_channel_included_right_after_signals_when_enabled():
    prompt = Prompt.chain(
        SignalsPrompt(None), ReactionPrompt(None), AudioPrompt(), TextPrompt(BASE_PROMPT), MemoryPrompt(_StubEnv()),
    )
    assert list(prompt.schema()) == ["signals", "reaction", "audio", "text", "memory"]


async def test_reaction_definition_text_reaches_the_built_prompt():
    """Regression: the 'reaction' channel's own definition alone never tells
    the model which reaction keys actually exist for this project — same
    role signal_definition plays for the 'signals' channel (see
    TrackingProcessor._build_reaction_definition, the only real caller)."""
    captured = {}

    class CapturingAiService:
        async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema):
            captured["prompt"] = system_prompt
            return
            yield  # pragma: no cover - never reached, makes this an async generator

    reaction_definition = '- Definition of reactions:\n\t- Reaction "supportive":\nUse when vulnerable.'
    prompt = Prompt.chain(
        SignalsPrompt(None), ReactionPrompt(reaction_definition), AudioPrompt(),
        TextPrompt(BASE_PROMPT), MemoryPrompt(_StubEnv()),
    )
    protocol = TurnProtocolUsingSchema(CapturingAiService())

    async for _ in protocol.generate_reply(prompt, HISTORY, lambda k, v: None):
        pass

    assert reaction_definition in captured["prompt"].full_text()
