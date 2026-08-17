"""Pins the streaming order of the four metadata channels
(signals/audio/text/env) TurnProtocol.__init__'s `evaluate_signals_first`
selects between (tracking/turn_protocol.py's `self.include_tags`):
"before" mode (`True`) -> signals, audio, text, env;
"after" mode (`False`) -> audio, text, signals, env.

"text" is never itself an on_metadata(key, ...) event in either
implementation (v1: no [text] tag is ever emitted, the model just emits
plain reply text; v2: ai_service.generate_stream_with_metadata explicitly
excludes "text" from on_metadata calls, see ai/ai_service.py:148) — its
position in the sequence is instead the moment the first visible reply
chunk is yielded. Each test builds a combined, ordered event log (real
metadata events plus that one synthetic "text" event) and asserts the
first-occurrence order of the four channels matches the mode's contract.
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
    """Shaped like the current ai.ai_service.AiService.
    generate_stream_with_metadata (see test_turn_strategy_compute_
    explicitly.py's own FakeAiServiceV2): replays a caller-supplied,
    ordered sequence of ("signals"/"audio"/"env", value) on_metadata
    calls and ("text", chunk) yields, in exactly the order given —
    lets a test dictate the full four-channel interleaving directly,
    without re-deriving it from raw JSON fragments."""

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
