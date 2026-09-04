"""SseChatTurn.on_metadata — the metadata-key allowlist an SSE-driven
turn (WhatsApp, test replay) forwards onto its own event queue as
`event: <type>\\ndata: <json>\\n\\n` (see chat/sse_turn.py's own
_stream). The WS-adapter equivalent (chat/ws_adapter.py) is off-limits
to edit — this is the one transport this event vocabulary can actually
be extended through today.
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


async def test_a_tool_call_event_forwards_its_status_text():
    turn = SseChatTurn(chat_service=None, session_id=1, text="hi")

    await turn.on_metadata("tool_call", {"status_text": "Searching Flights…"})

    assert await _drain(turn) == [("tool_call", {"status_text": "Searching Flights…"})]


async def test_a_tool_result_event_carries_no_payload():
    turn = SseChatTurn(chat_service=None, session_id=1, text="hi")

    await turn.on_metadata("tool_result", {"name": "source_flights_select", "arguments": {}, "result": "row"})

    assert await _drain(turn) == [("tool_result", {})]


async def test_an_unrecognized_metadata_key_is_silently_dropped():
    turn = SseChatTurn(chat_service=None, session_id=1, text="hi")

    await turn.on_metadata("something_new", {"whatever": True})

    assert await _drain(turn) == []
