from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, cast

import pytest

from ai._providers.cascading_llm_provider import AutoTestLLMProvider
from ai.ai_service import AiService
from ai.llm_provider import AIServiceProviderPermanentError, LLMProvider, MetadataCallback
from system.usage_account import ProjectAccount, SessionAccount

pytestmark = pytest.mark.regression

SESSION = {"id": 7, "type": "live", "username": "alice", "project_id": "hello"}


class _Answers(LLMProvider):
    async def stream_json(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ) -> AsyncIterator[str]:
        report = cast(MetadataCallback, on_metadata)
        yield json.dumps({"text": "hi"})
        report("thoughts_tokens", 3)
        report("input_tokens", 10)
        report("output_tokens", 5)

    def get_input_tokens(self, prompt: str) -> int:
        return 0


class _Fails(LLMProvider):
    async def stream_json(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ) -> AsyncIterator[str]:
        raise AIServiceProviderPermanentError("down")
        yield ""

    def get_input_tokens(self, prompt: str) -> int:
        return 0


def _totals(db, *group_by: str) -> list[dict]:
    return sorted(db.get_ai_usage_totals(group_by), key=lambda row: (row["kind"], row["provider_label"]))


def test_a_call_through_the_plain_service_is_recorded_as_unattributed(db):
    asyncio.run(AiService(_Answers(), db=db).prompt("q"))

    assert [(row["kind"], row["project_id"]) for row in _totals(db, "project_id")] == [("unattributed", None)]


def test_a_session_s_calls_are_summed_by_session_with_every_kind_of_token(db):
    service = AiService(_Answers(), db=db).charged_to(SessionAccount(SESSION))

    asyncio.run(service.prompt("q"))
    asyncio.run(service.prompt("q again"))

    row, = _totals(db, "session_id", "username", "project_id")
    assert (row["kind"], row["session_id"], row["username"], row["project_id"]) == ("session", 7, "alice", "hello")
    assert (row["calls"], row["input_tokens"], row["output_tokens"], row["thoughts_tokens"]) == (2, 20, 10, 6)


def test_work_outside_a_conversation_is_charged_to_the_project_and_its_user(db):
    asyncio.run(AiService(_Answers(), db=db).charged_to(ProjectAccount("hello", "bob")).prompt("edit"))

    row, = _totals(db, "project_id", "username")
    assert (row["kind"], row["project_id"], row["username"]) == ("project", "hello", "bob")


def test_the_tokens_are_filed_under_the_provider_that_answered_not_the_one_that_failed(db):
    cascade = AutoTestLLMProvider([("x/fails", _Fails()), ("y/answers", _Answers())])

    asyncio.run(AiService(cascade, db=db).charged_to(SessionAccount(SESSION)).prompt("q"))

    assert [row["provider_label"] for row in _totals(db, "session_id")] == ["y/answers"]


def test_a_charged_service_follows_a_model_chosen_after_it_was_charged():
    service = AiService(_Answers(), selectable_providers=[_Answers()])
    charged = service.charged_to(SessionAccount(SESSION))

    service.select_model(0)

    assert charged.get_models_snapshot()["auto"] is False


def test_grouping_by_an_unknown_column_is_refused(db):
    with pytest.raises(ValueError):
        db.get_ai_usage_totals(("provider_label",))
