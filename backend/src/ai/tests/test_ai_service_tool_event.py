"""AiService's own tool-call loop emits exactly one on_metadata("tool", ...)
event per call per phase — "start" right before ToolSet.call, "result"
right after — composed entirely by ToolSet.tool_event (see
tracking.sources.ToolSet.tool_event's own docstring). AiService itself
never builds the payload, only forwards round/result/duration_ms through
tool_event's own **result_fields.
"""
from __future__ import annotations

import pytest

from ai.ai_service import AiService
from ai.llm_provider import ToolCall, ToolCallsRequested, ToolSpec

_SELECT_SPEC = ToolSpec(
    name="source_flights_select",
    description="Grep over the flights archive.",
    parameters={"type": "object", "properties": {"values": {"type": "array", "items": {"type": "string"}}}, "required": ["values"]},
)


class _FakeToolSet:
    """A tool_event exactly as real ToolSet.tool_event composes it, minus
    any real source/driver lookup — enough for AiService's own loop to
    drive without a real automaton/db."""

    def __init__(self, results: dict[str, str]) -> None:
        self.session_id = 7
        self._results = results

    def specs(self) -> list[ToolSpec]:
        return [_SELECT_SPEC]

    def required_specs(self) -> list[ToolSpec]:
        return []

    def tool_event(self, name: str, arguments: dict, phase: str, **result_fields) -> dict:
        payload = {
            "phase": phase, "name": name, "source": "flights", "method": "select",
            "label": "Flights", "description": None, "arguments": arguments, "round": result_fields.get("round"),
        }
        if phase == "result":
            result = result_fields["result"]
            error = result.startswith("error:")
            payload.update({
                "result": result, "rows": 0 if error else max(0, len(result.splitlines()) - 1),
                "error": error, "duration_ms": result_fields.get("duration_ms"),
            })
        return payload

    async def call(self, name: str, arguments: dict) -> str:
        return self._results[arguments["values"][0]]


class _OneRoundProvider:
    def __init__(self, calls: list[ToolCall]) -> None:
        self._round = 0
        self._calls = calls

    async def generate_stream_with_schema(self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None):
        self._round += 1
        if self._round == 1:
            raise ToolCallsRequested(calls=self._calls, assistant_content=None)
        yield '{"text": "done"}'

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0


async def _run(provider, tool_set) -> list[tuple[str, object]]:
    ai_service = AiService(provider)
    events: list[tuple[str, object]] = []
    async for _ in ai_service.generate_stream_with_metadata(
        "sys", [], on_metadata=lambda k, v: events.append((k, v)), schema={"text": "t"}, tool_set=tool_set,
    ):
        pass
    return events


async def test_a_single_call_emits_start_then_result_then_chunks():
    calls = [ToolCall(id="1", name="source_flights_select", arguments={"values": ["paris"]})]
    provider = _OneRoundProvider(calls)
    tool_set = _FakeToolSet({"paris": "city,country\nParis,France\n"})

    events = await _run(provider, tool_set)

    tool_events = [event for event in events if event[0] == "tool"]
    assert len(tool_events) == 2
    assert tool_events[0][1]["phase"] == "start"
    assert tool_events[0][1]["arguments"] == {"values": ["paris"]}
    assert tool_events[1][1]["phase"] == "result"
    assert tool_events[1][1]["result"] == "city,country\nParis,France\n"
    assert tool_events[1][1]["rows"] == 1
    assert tool_events[1][1]["error"] is False
    assert isinstance(tool_events[1][1]["duration_ms"], int)


async def test_two_calls_in_the_same_round_emit_four_events_in_order():
    calls = [
        ToolCall(id="1", name="source_flights_select", arguments={"values": ["paris"]}),
        ToolCall(id="2", name="source_flights_select", arguments={"values": ["orly"]}),
    ]
    provider = _OneRoundProvider(calls)
    tool_set = _FakeToolSet({"paris": "city\nParis\n", "orly": "city\nOrly\n"})

    events = await _run(provider, tool_set)

    tool_events = [event for event in events if event[0] == "tool"]
    phases_and_args = [(e[1]["phase"], e[1]["arguments"]["values"][0]) for e in tool_events]
    assert phases_and_args == [
        ("start", "paris"), ("result", "paris"), ("start", "orly"), ("result", "orly"),
    ]


async def test_a_driver_exception_still_reports_a_result_event_with_error_true_and_the_turn_continues():
    calls = [ToolCall(id="1", name="source_flights_select", arguments={"values": ["boom"]})]
    provider = _OneRoundProvider(calls)
    tool_set = _FakeToolSet({"boom": "error: driver exploded"})

    events = await _run(provider, tool_set)

    tool_events = [event for event in events if event[0] == "tool"]
    result_event = tool_events[1][1]
    assert result_event["error"] is True
    assert result_event["result"] == "error: driver exploded"
    assert result_event["rows"] == 0


async def test_the_turn_still_streams_the_final_answer_after_a_tool_call():
    calls = [ToolCall(id="1", name="source_flights_select", arguments={"values": ["paris"]})]
    provider = _OneRoundProvider(calls)
    tool_set = _FakeToolSet({"paris": "city\nParis\n"})
    ai_service = AiService(provider)

    chunks = [
        chunk async for chunk in ai_service.generate_stream_with_metadata(
            "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        )
    ]

    assert "".join(chunks) == "done"
