"""AiService logs an INFO line for every tool call it resolves (session,
round, name, arguments, result length, duration) and one more at the end
of a tool-using turn (rounds used, summed input tokens) — see
generate_stream_with_metadata's own tool-call loop.
"""
from __future__ import annotations

import logging

from ai.ai_service import AiService
from ai.llm_provider import ToolCall, ToolCallsRequested, ToolSpec

_SELECT_SPEC = ToolSpec(
    name="source_flights_select",
    description="Grep over the flights archive.",
    parameters={"type": "object", "properties": {"values": {"type": "array", "items": {"type": "string"}}}, "required": ["values"]},
)


class _FakeToolSet:
    def __init__(self) -> None:
        self.session_id = 7

    def specs(self) -> list[ToolSpec]:
        return [_SELECT_SPEC]

    def required_specs(self) -> list[ToolSpec]:
        return []

    def tool_event(self, name: str, arguments: dict, phase: str, **result_fields) -> dict:
        payload = {
            "phase": phase, "name": name, "source": name, "method": None,
            "label": name, "description": None, "arguments": arguments, "round": result_fields.get("round"),
        }
        if phase == "result":
            result = result_fields.get("result", "")
            error = result.startswith("error:")
            payload.update({
                "result": result, "rows": 0 if error else max(0, len(result.splitlines()) - 1),
                "error": error, "duration_ms": result_fields.get("duration_ms"),
            })
        return payload

    async def call(self, name: str, arguments: dict) -> str:
        return "city,country\nParis,France\n"


class _FakeProvider:
    """One tool-call round, then a final answer — just enough for
    AiService's own loop to log both a per-call line and the end-of-turn summary."""

    def __init__(self) -> None:
        self._round = 0

    async def generate_stream_with_schema(self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None):
        self._round += 1
        if on_metadata is not None:
            on_metadata("input_tokens", 10 * self._round)
            on_metadata("output_tokens", 5)
        if self._round == 1:
            raise ToolCallsRequested(
                calls=[ToolCall(id="call_1", name="source_flights_select", arguments={"values": ["paris"]})],
                assistant_content=None,
            )
        yield '{"text": "Paris it is."}'

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0


async def test_a_tool_call_is_logged_at_info(caplog):
    ai_service = AiService(_FakeProvider())
    tool_set = _FakeToolSet()

    with caplog.at_level(logging.INFO, logger="ai.ai_service"):
        async for _ in ai_service.generate_stream_with_metadata(
            "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        ):
            pass

    tool_call_lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("tool call:")]
    assert len(tool_call_lines) == 1
    line = tool_call_lines[0]
    assert "session=7" in line
    assert "round=1" in line
    assert "name=source_flights_select" in line
    assert "arguments=" in line
    assert "result_chars=" in line
    assert "duration_ms=" in line


async def test_the_end_of_turn_summary_reports_rounds_and_summed_input_tokens(caplog):
    ai_service = AiService(_FakeProvider())
    tool_set = _FakeToolSet()

    with caplog.at_level(logging.INFO, logger="ai.ai_service"):
        async for _ in ai_service.generate_stream_with_metadata(
            "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        ):
            pass

    summary_lines = [r.getMessage() for r in caplog.records if "turn done" in r.getMessage()]
    assert len(summary_lines) == 1
    assert "rounds=2" in summary_lines[0]
    # Round 1 reported input_tokens=10, round 2 reported input_tokens=20 (self._round * 10).
    assert "total_input_tokens=30" in summary_lines[0]
    # Neither round reported cache tokens here — summed to 0, not omitted.
    assert "cache_read_tokens=0" in summary_lines[0]
    assert "cache_creation_tokens=0" in summary_lines[0]
