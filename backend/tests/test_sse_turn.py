"""SseChatTurn.on_metadata — the metadata-key allowlist a chat turn
forwards onto its own event queue as `event: <type>\\ndata: <json>\\n\\n`
(see chat/sse_turn.py's own _stream). SSE is chat's only transport now —
ws_notifications.py is a push-only notification channel with no turn traffic
of its own.
"""
from __future__ import annotations

import pytest

from chat.sse_turn import SseChatTurn

pytestmark = pytest.mark.contract


async def _drain(turn: SseChatTurn) -> list[tuple[str, dict]]:
    events = []
    while not turn._events.empty():
        events.append(turn._events.get_nowait())
    return events


async def test_an_unrecognized_metadata_key_is_silently_dropped():
    turn = SseChatTurn(chat_service=None, session_id=1, text="hi")

    await turn.on_metadata("something_new", {"whatever": True})

    assert await _drain(turn) == []


def _tool_event(phase: str, **overrides) -> dict:
    event = {
        "phase": phase, "name": "source_flights_select", "source": "flights", "method": "select",
        "label": "Flights", "description": None, "arguments": {"values": ["VY3003"]}, "round": 1,
    }
    event.update(overrides)
    return event


async def test_a_tool_start_event_forwards_the_structured_fields_plus_status_text():
    turn = SseChatTurn(chat_service=None, session_id=1, text="hi")
    payload = _tool_event("start")

    await turn.on_metadata("tool", payload)

    [(event, data)] = await _drain(turn)
    assert event == "tool"
    assert data == {**payload, "status_text": 'Searching Flights for "VY3003"…'}


async def test_a_tool_result_event_forwards_the_full_payload_with_no_status_text():
    turn = SseChatTurn(chat_service=None, session_id=1, text="hi")
    payload = _tool_event("result", result="city,country\nParis,France\n", rows=1, error=False, duration_ms=12)

    await turn.on_metadata("tool", payload)

    assert await _drain(turn) == [("tool", payload)]
