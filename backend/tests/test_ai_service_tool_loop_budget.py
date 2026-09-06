"""AiService._enforce_input_budget's own two bugs, now fixed:

1. It used to call content_to_text(message["content"]) on every message
   in turn_history, including a tool-calling round's own {"role":
   "assistant", "tool_calls": [...], "content": ...} message — whose
   `content` is None (Anthropic/OpenAI, nothing said before the call) or
   a provider-specific opaque replay payload (e.g. Gemini's own
   {"gemini_parts": [...]}). content_to_text can't handle either shape
   and raised TypeError on round 2+ of every tool-calling turn. The fix
   is a dedicated estimate_history_tokens(turn_history), which content_to_text
   itself was never touched for (see its own module, unchanged).
2. When a later round does go over budget, it now also saves a
   SystemWarning(kind="input_budget_exceeded") naming the heaviest
   accumulated tool result(s), mirroring TrackingProcessor's own sibling
   check on base_prompt/signal_definition/reaction_definition.
"""
from __future__ import annotations

from http import HTTPStatus

import pytest

from ai.ai_service import AiService
from ai.llm_provider import ToolCall, ToolCallsRequested, ToolSpec
from chat.errors import ChatServiceError

_SELECT_SPEC = ToolSpec(
    name="source_flights_select",
    description="Grep over the flights archive.",
    parameters={"type": "object", "properties": {"values": {"type": "array", "items": {"type": "string"}}}, "required": ["values"]},
)

PROJECT_ID = "proj"


class _FakeToolSet:
    def __init__(self, result: str = "city,country\nParis,France\n") -> None:
        self.session_id = 7
        self.project_id = PROJECT_ID
        self._result = result

    def specs(self) -> list[ToolSpec]:
        return [_SELECT_SPEC]

    def required_specs(self) -> list[ToolSpec]:
        return []

    async def call(self, name: str, arguments: dict) -> str:
        return self._result


class _TwoRoundProvider:
    """Round 1 always asks for a tool call with `assistant_content`
    (None, or a provider-specific opaque dict); round 2 always answers."""

    def __init__(self, assistant_content) -> None:
        self._round = 0
        self._assistant_content = assistant_content

    async def generate_stream_with_schema(self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None):
        self._round += 1
        if self._round == 1:
            raise ToolCallsRequested(
                calls=[ToolCall(id="call_1", name="source_flights_select", arguments={"values": ["paris"]})],
                assistant_content=self._assistant_content,
            )
        yield '{"text": "Paris it is."}'

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0


async def _run(provider, tool_set, budget=None, db=None) -> str:
    ai_service = AiService(provider, db=db, input_token_budget_per_turn=budget)
    chunks = []
    async for chunk in ai_service.generate_stream_with_metadata(
        "sys", [], on_metadata=lambda k, v: None, schema={"text": "t"}, tool_set=tool_set,
    ):
        chunks.append(chunk)
    return "".join(chunks)


async def test_a_text_less_tool_call_round_never_raises_type_error_and_round_2_runs():
    reply = await _run(_TwoRoundProvider(assistant_content=None), _FakeToolSet(), budget=100_000)

    assert "Paris it is." in reply


async def test_a_gemini_replay_payload_never_raises_type_error_and_round_2_runs():
    reply = await _run(
        _TwoRoundProvider(assistant_content={"gemini_parts": ["opaque", "parts"]}), _FakeToolSet(), budget=100_000,
    )

    assert "Paris it is." in reply


async def test_round_2_over_budget_raises_413_and_saves_a_system_warning(db):
    # A budget round 1 alone comfortably fits under, but round 2 (with the
    # tool call's own arguments + a deliberately large accumulated result)
    # blows past.
    tool_set = _FakeToolSet(result="x" * 3000)

    with pytest.raises(ChatServiceError) as exc_info:
        await _run(_TwoRoundProvider(assistant_content=None), tool_set, budget=50, db=db)

    assert exc_info.value.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert exc_info.value.code == "input_budget_exceeded"
    warnings = db.get_system_warnings("user", PROJECT_ID)
    assert len(warnings) == 1
    assert warnings[0]["kind"] == "input_budget_exceeded"
    assert "source_flights_select" in warnings[0]["message"]
    assert "round 2" in warnings[0]["message"]
