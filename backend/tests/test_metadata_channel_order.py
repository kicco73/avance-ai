"""Pins the streaming order of the four metadata channels
(signals/audio/text/env): "before" mode -> signals, audio, text, env;
"after" mode -> audio, text, signals, env. "text" is never itself an
on_metadata event — its position is the moment the first reply chunk is yielded.
"""
from __future__ import annotations

import pytest

from tracking.turn_protocol_using_schema import TurnProtocolUsingSchema
from tracking.turn_protocol_using_text_extraction import TurnProcotolUsingTextExtraction

pytestmark = pytest.mark.contract


class _StubEnv:
    def serialise_as_text(self) -> str:
        return ""


BASE_PROMPT = "You are a helpful assistant."
HISTORY = [{"role": "user", "content": "hi"}]


class FakeAiServiceV1:
    """generate_stream yields a caller-supplied sequence of chunks
    verbatim — lets a test dictate exactly where each [tag]...[/tag]
    closes relative to the plain-text portion of the reply."""

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    def is_provider_with_schema(self) -> bool:
        return False

    async def generate_stream(self, system_prompt, history):
        for chunk in self._chunks:
            yield chunk


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


async def _run_v1(evaluate_signals_first: bool, chunks: list[str]) -> list[str]:
    ai_service = FakeAiServiceV1(chunks)
    protocol = TurnProcotolUsingTextExtraction(ai_service, evaluate_signals_first)

    events: list[str] = []
    text_seen = False

    def on_metadata(key: str, value) -> None:
        events.append(key)

    async for chunk in protocol.generate_reply(BASE_PROMPT, None, _StubEnv(), HISTORY, on_metadata):
        if chunk.strip() and not text_seen:
            events.append("text")
            text_seen = True

    return events


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


# --- v1 (text extraction) ---

async def test_v1_before_mode_orders_signals_audio_text_env():
    chunks = [
        "[signals][/signals]",
        "[audio]hi there[/audio]",
        "some visible reply text ",
        "[env]k: v[/env]",
    ]
    events = await _run_v1(True, chunks)
    assert _first_occurrence_order(events) == ["signals", "audio", "text", "env"]


async def test_v1_after_mode_orders_audio_text_signals_env():
    chunks = [
        "[audio]hi there[/audio]",
        "some visible reply text ",
        "[signals][/signals]",
        "[env]k: v[/env]",
    ]
    events = await _run_v1(False, chunks)
    assert _first_occurrence_order(events) == ["audio", "text", "signals", "env"]


async def test_v1_metadata_events_report_under_their_own_tag_name():
    """Regression for the closure late-binding bug: every tag's callback
    used to report under whichever tag happened to be last in
    include_tags, regardless of which tag actually closed."""
    chunks = ["[audio]a[/audio]", "[signals]s[/signals]", "[env]e[/env]"]
    ai_service = FakeAiServiceV1(chunks)
    protocol = TurnProcotolUsingTextExtraction(ai_service, True)

    reported: dict[str, str] = {}

    def on_metadata(key: str, value) -> None:
        reported[key] = value

    async for _ in protocol.generate_reply(BASE_PROMPT, None, _StubEnv(), HISTORY, on_metadata):
        pass

    assert reported == {"audio": "a", "signals": "s", "env": "e"}


# --- v2 (schema) ---

async def test_v2_before_mode_orders_signals_audio_text_env():
    scripted = [
        ("signals", "{}"),
        ("audio", "hi there"),
        ("text", "some visible reply text"),
        ("env", "k: v"),
    ]
    events = await _run_v2(True, scripted)
    assert _first_occurrence_order(events) == ["signals", "audio", "text", "env"]


async def test_v2_after_mode_orders_audio_text_signals_env():
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
    assert "reaction" not in TurnProcotolUsingTextExtraction(FakeAiServiceV1([]), False).include_tags


def test_reaction_tag_included_right_after_signals_when_enabled():
    before = TurnProtocolUsingSchema(FakeAiServiceV2([]), True, reactions_enabled=True)
    assert before.include_tags == ("signals", "reaction", "audio", "text", "env")

    after = TurnProcotolUsingTextExtraction(FakeAiServiceV1([]), False, reactions_enabled=True)
    assert after.include_tags == ("audio", "text", "signals", "reaction", "env")


async def test_reaction_definition_text_reaches_the_built_prompt():
    """Regression: the 'reaction' tag's own preamble alone never tells the
    model which reaction keys actually exist for this project — same role
    signal_definition plays for the 'signals' tag (see
    TrackingProcessor._build_reaction_definition, the only real caller)."""
    captured = {}

    class CapturingAiService:
        def is_provider_with_schema(self) -> bool:
            return False

        async def generate_stream(self, system_prompt, history):
            captured["prompt"] = system_prompt
            return
            yield  # pragma: no cover - never reached, makes this an async generator

    protocol = TurnProcotolUsingTextExtraction(CapturingAiService(), True, reactions_enabled=True)
    reaction_definition = '- Definition of reactions:\n\t- Reaction "supportive":\nUse when vulnerable.'

    async for _ in protocol.generate_reply(
        BASE_PROMPT, None, _StubEnv(), HISTORY, lambda k, v: None, reaction_definition=reaction_definition,
    ):
        pass

    assert reaction_definition in captured["prompt"]
