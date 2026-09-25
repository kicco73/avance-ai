from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, cast

import pytest

pytest.importorskip("ai.ai_service")

from ai.ai_service import AiService
from ai.llm_provider import LLMProvider, MetadataCallback
from system.usage_account import SessionAccount
from testing.benchmark_account import BenchmarkAccount

pytestmark = pytest.mark.regression

SESSION = {"id": 7, "type": "live", "username": "alice", "project_id": "hello"}


class _Answers(LLMProvider):
    async def stream_json(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ) -> AsyncIterator[str]:
        report = cast(MetadataCallback, on_metadata)
        yield json.dumps({"text": "hi"})
        report("input_tokens", 10)
        report("output_tokens", 5)

    def get_input_tokens(self, prompt: str) -> int:
        return 0


def _totals(db, *group_by: str) -> list[dict]:
    return sorted(db.get_ai_usage_totals(group_by), key=lambda row: (row["kind"], row["provider_label"]))


def test_a_benchmark_replay_of_a_session_is_a_line_of_its_own(db):
    service = AiService(_Answers(), db=db)
    asyncio.run(service.charged_to(SessionAccount(SESSION)).prompt("live"))
    asyncio.run(service.charged_to(BenchmarkAccount({"id": 3, "project_id": "hello"}, SESSION)).prompt("replay"))

    rows = _totals(db, "session_id", "test_run_id")
    assert [(row["kind"], row["session_id"], row["test_run_id"], row["input_tokens"]) for row in rows] == [
        ("benchmark", 7, 3, 10), ("session", 7, None, 10),
    ]
