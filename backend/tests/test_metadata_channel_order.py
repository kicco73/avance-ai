"""Pins the streaming order of the four metadata channels
(signals/audio/text/env): "before" mode -> signals, audio, text, env;
"after" mode -> audio, text, signals, env. "text" is never itself an
on_metadata event — its position is the moment the first reply chunk is yielded.
"""
from __future__ import annotations

import pytest

from tracking.turn_protocol_using_schema import TurnProtocolUsingSchema

pytestmark = pytest.mark.contract


class _StubEnv:
    def serialise_as_text(self) -> str:
        return ""


BASE_PROMPT = "You are a helpful assistant."
HISTORY = [{"role": "user", "content": "hi"}]


class FakeAiServiceV2:
    """Replays a caller-supplied, ordered sequence of ("signals"/"audio"/
    "env", value) on_metadata calls and ("text", chunk) yields, letting a
    test dictate the full four-channel interleaving directly."""

    def __init__(self, events: list[tuple[str, str]]) -> None:
        self._events = events

    def is_provider_with_schema(self) -> bool:
        return True

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema):
        for kind, value in self._events:
            if kind == "text":
                yield value
            else:
                on_metadata(kind, value)


async def _run_v2(evaluate_signals_first: bool, events: list[tuple[str, str]]) -> list[str]:
    ai_service = FakeAiServiceV2(events)
    protocol = TurnProtocolUsingSchema(ai_service, evaluate_signals_first)

    events: list[str] = []
    text_seen = False

    def on_metadata(key: str, value) -> None:
        events.append(key)

    async for chunk in protocol.generate_reply(BASE_PROMPT, None, _StubEnv(), HISTORY, on_metadata):
        if chunk.strip() and not text_seen:
            events.append("text")
            text_seen = True

    return events


def _first_occurrence_order(events: list[str]) -> list[str]:
    seen: list[str] = []
    for event in events:
        if event not in seen:
            seen.append(event)
    return seen


async def test_before_mode_orders_signals_audio_text_env():
    scripted = [
        ("signals", "{}"),
        ("audio", "hi there"),
        ("text", "some visible reply text"),
        ("env", "k: v"),
    ]
    events = await _run_v2(True, scripted)
    assert _first_occurrence_order(events) == ["signals", "audio", "text", "env"]


async def test_after_mode_orders_audio_text_signals_env():
    scripted = [
        ("audio", "hi there"),
        ("text", "some visible reply text"),
        ("signals", "{}"),
        ("env", "k: v"),
    ]
    events = await _run_v2(False, scripted)
    assert _first_occurrence_order(events) == ["audio", "text", "signals", "env"]


# --- 'reaction' tag: excluded by default, inserted right after 'signals' ---

def test_reaction_tag_excluded_by_default():
    assert "reaction" not in TurnProtocolUsingSchema(FakeAiServiceV2([]), True).include_tags


def test_reaction_tag_included_right_after_signals_when_enabled():
    before = TurnProtocolUsingSchema(FakeAiServiceV2([]), True, reactions_enabled=True)
    assert before.include_tags == ("signals", "reaction", "audio", "text", "env")


async def test_reaction_definition_text_reaches_the_built_prompt():
    """Regression: the 'reaction' tag's own preamble alone never tells the
    model which reaction keys actually exist for this project — same role
    signal_definition plays for the 'signals' tag (see
    TrackingProcessor._build_reaction_definition, the only real caller)."""
    captured = {}

    class CapturingAiService:
        def is_provider_with_schema(self) -> bool:
            return True

        async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema):
            captured["prompt"] = system_prompt
            return
            yield  # pragma: no cover - never reached, makes this an async generator

    protocol = TurnProtocolUsingSchema(CapturingAiService(), True, reactions_enabled=True)
    reaction_definition = '- Definition of reactions:\n\t- Reaction "supportive":\nUse when vulnerable.'

    async for _ in protocol.generate_reply(
        BASE_PROMPT, None, _StubEnv(), HISTORY, lambda k, v: None, reaction_definition=reaction_definition,
    ):
        pass

    assert reaction_definition in captured["prompt"]
