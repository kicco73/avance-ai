"""Every tool-calling round streams live through the same incremental
partial-JSON parser as a no-tool-set turn, instead of being collected
into a list and only replayed once the whole round (and any further
round) has finished — see AiService.generate_stream_with_metadata's own
docstring. These pin: a round's own text/metadata reach the caller before
a later round even starts (not just "eventually, once the turn ends");
tool_abort discards a round's own tool calls instead of resolving them;
and a schema field the parser only gets to see once (a round cut short by
ToolCallsRequested) is still delivered exactly once, never re-delivered by
the next round's own from-scratch parser.
"""
from __future__ import annotations

import asyncio

from ai.ai_service import AiService
from ai.llm_provider import ToolCall, ToolCallsRequested, ToolSpec

_SPEC = ToolSpec(
    name="source_flights_select",
    description="Grep over the flights archive.",
    parameters={"type": "object", "properties": {"values": {"type": "array", "items": {"type": "string"}}}, "required": ["values"]},
)


class _FakeToolSet:
    def __init__(self, log: list[str]) -> None:
        self.session_id = 1
        self.log = log

    def specs(self) -> list[ToolSpec]:
        return [_SPEC]

    def required_specs(self) -> list[ToolSpec]:
        return []

    def status_text(self, name: str) -> str:
        return "Searching…"

    def summary_text(self, name: str, arguments: dict, result: str) -> str:
        return "Searched · 1 row"

    async def call(self, name: str, arguments: dict) -> str:
        self.log.append("tool_call_started")
        await asyncio.sleep(0)
        self.log.append("tool_call_finished")
        return "city,country\nParis,France\n"


class _ToolAbort:
    def __init__(self, abort: bool) -> None:
        self._abort = abort

    def should_abort_tools(self) -> bool:
        return self._abort


class _RoundTextThenToolProvider:
    """Round 1 streams a partial "text" delta, then asks for a tool
    (never completing its own JSON); round 2 completes with more text."""

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self._round = 0

    async def generate_stream_with_schema(self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None):
        self._round += 1
        if self._round == 1:
            self.log.append("round1_yield")
            yield '{"text": "Hel'
            await asyncio.sleep(0)
            raise ToolCallsRequested(
                calls=[ToolCall(id="call_1", name="source_flights_select", arguments={"values": ["paris"]})],
                assistant_content=None,
            )
        yield '{"text": "lo world"}'

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0


async def test_a_round_cut_short_by_a_tool_call_still_streams_its_own_text_live():
    log: list[str] = []
    ai_service = AiService(_RoundTextThenToolProvider(log))
    tool_set = _FakeToolSet(log)

    chunks: list[str] = []
    async for chunk in ai_service.generate_stream_with_metadata(
        "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
    ):
        log.append(f"consumer_got:{chunk!r}")
        chunks.append(chunk)

    assert chunks == ["Hel", "lo world"]
    # Round 1's own delta must reach the caller before the tool even runs
    # — under the old "collect the whole round, then decide" design it
    # never reached the caller at all (only the final round, replayed
    # after full collection, ever streamed).
    assert log.index("consumer_got:'Hel'") < log.index("tool_call_started")


async def test_tool_abort_discards_the_round_without_ever_calling_the_tool():
    log: list[str] = []
    ai_service = AiService(_RoundTextThenToolProvider(log))
    tool_set = _FakeToolSet(log)

    chunks: list[str] = []
    async for chunk in ai_service.generate_stream_with_metadata(
        "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
        tool_abort=_ToolAbort(abort=True),
    ):
        chunks.append(chunk)

    # Round 1's own partial text still streamed live (it arrived before
    # the abort was even decided) — only the tool call itself, and any
    # further round, are the things tool_abort actually prevents.
    assert chunks == ["Hel"]
    assert "tool_call_started" not in log


async def test_tool_abort_false_behaves_exactly_like_no_tool_abort_at_all():
    log: list[str] = []
    ai_service = AiService(_RoundTextThenToolProvider(log))
    tool_set = _FakeToolSet(log)

    chunks = [
        chunk async for chunk in ai_service.generate_stream_with_metadata(
            "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
            tool_abort=_ToolAbort(abort=False),
        )
    ]

    assert chunks == ["Hel", "lo world"]
    assert "tool_call_started" in log


class _FieldCutShortByToolProvider:
    """Round 1 completes a non-text field (audio) but is then interrupted
    by a tool call before ever emitting a superseding key — the one case
    the live "completed" check inside the loop can't catch on its own
    (see _stream_final_answer's own _deliver_last_field). Round 2 restates
    the same field (a real provider wouldn't do this — the model already
    said it — but it pins that emitted is honored across rounds even so)
    alongside the turn's actual text."""

    def __init__(self) -> None:
        self._round = 0

    async def generate_stream_with_schema(self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None):
        self._round += 1
        if self._round == 1:
            yield '{"audio": "round1-audio"'
            raise ToolCallsRequested(
                calls=[ToolCall(id="call_1", name="source_flights_select", arguments={"values": ["paris"]})],
                assistant_content=None,
            )
        yield '{"audio": "round1-audio", "text": "final answer"}'

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0


async def test_a_field_completed_in_a_tool_interrupted_round_is_delivered_exactly_once():
    log: list[str] = []
    tool_set = _FakeToolSet(log)
    metadata_calls: list[tuple[str, object]] = []
    ai_service = AiService(_FieldCutShortByToolProvider())

    chunks = [
        chunk async for chunk in ai_service.generate_stream_with_metadata(
            "sys", [], on_metadata=lambda k, v: metadata_calls.append((k, v)), schema={"audio": "a", "text": "t"},
            tool_set=tool_set,
        )
    ]

    assert chunks == ["final answer"]
    audio_deliveries = [v for k, v in metadata_calls if k == "audio"]
    assert audio_deliveries == ["round1-audio"]
