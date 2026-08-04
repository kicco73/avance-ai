"""Tests for TurnStrategyV1.compute_explicitly/TurnStrategyV2.compute_explicitly
— each strategy's own dedicated "no reply to piggyback on" call (see
tracking.auto_tracker.AutoTracker.run's own explicit-fallback branch),
each recovering raw signal values in its own dialect: tags for v1
(chat.metadata_handler), on_metadata for v2 (chat.turn_strategy_v2's
gemini_provider_v2.py's own STRING-typed schema). Both return the raw,
unvalidated {name: value} dict — see test_signal_evaluator.py for
SignalEvaluator.validate's own coercion against an automaton's declared
signals, no longer done inside either of these.
"""
from __future__ import annotations

from ai.llm_provider import AIServiceError
from chat.env import Env
from chat.turn_protocol_using_text_extraction import TurnProcotolUsingTextExtraction
from chat.turn_protocol_using_schema import TurnProtocolUsingSchema

USERNAME = "user"
PROJECT_NAME = "proj"


def _env(db) -> Env:
    return Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)


class FakeAiServiceV1:
    def __init__(self, reply: str | None = None, error: Exception | None = None) -> None:
        self._reply = reply
        self._error = error

    async def generate(self, system_prompt, history, on_retry=None, on_metadata=None) -> str:
        if self._error is not None:
            raise self._error
        return self._reply


class FakeAiServiceV2:
    """Mimics a v2 provider's own generate(): calls on_metadata once per
    configured key (a plain string, same as gemini_provider_v2.py's own
    STRING-typed schema actually sends) before returning."""

    def __init__(self, metadata: dict | None = None, error: Exception | None = None) -> None:
        self._metadata = metadata or {}
        self._error = error

    async def generate(self, system_prompt, history, on_retry=None, on_metadata=None) -> str:
        if self._error is not None:
            raise self._error
        if on_metadata is not None:
            for key, value in self._metadata.items():
                on_metadata(key, value)
        return ""


async def test_v1_compute_explicitly_extracts_from_a_signals_tag(db):
    strategy = TurnProcotolUsingTextExtraction(FakeAiServiceV1(reply='Hi![signals]{"mood": 75}[/signals]'))

    result = await strategy.compute_explicitly("- Definition of signals: ...", _env(db), [])

    assert result == {"mood": 75}


async def test_v1_compute_explicitly_degrades_to_empty_dict_on_ai_failure(db):
    strategy = TurnProcotolUsingTextExtraction(FakeAiServiceV1(error=AIServiceError("boom")))

    result = await strategy.compute_explicitly("- Definition of signals: ...", _env(db), [])

    assert result == {}


async def test_v1_compute_explicitly_with_no_signals_tag_at_all_is_empty(db):
    strategy = TurnProcotolUsingTextExtraction(FakeAiServiceV1(reply="just a plain reply, no tags"))

    result = await strategy.compute_explicitly("- Definition of signals: ...", _env(db), [])

    assert result == {}


async def test_v2_compute_explicitly_json_parses_the_signals_field(db):
    strategy = TurnProtocolUsingSchema(FakeAiServiceV2(metadata={"signals": '{"mood": 75}'}))

    result = await strategy.compute_explicitly("- Definition of signals: ...", _env(db), [])

    assert result == {"mood": 75}


async def test_v2_compute_explicitly_ignores_audio_and_env(db):
    strategy = TurnProtocolUsingSchema(
        FakeAiServiceV2(metadata={"audio": "hi", "env": '{"x": "y"}', "signals": '{"mood": 1}'})
    )

    result = await strategy.compute_explicitly("- Definition of signals: ...", _env(db), [])

    assert result == {"mood": 1}


async def test_v2_compute_explicitly_degrades_to_empty_dict_on_ai_failure(db):
    strategy = TurnProtocolUsingSchema(FakeAiServiceV2(error=AIServiceError("boom")))

    result = await strategy.compute_explicitly("- Definition of signals: ...", _env(db), [])

    assert result == {}


async def test_v2_compute_explicitly_with_no_signals_field_at_all_is_empty(db):
    strategy = TurnProtocolUsingSchema(FakeAiServiceV2(metadata={"audio": "hi"}))

    result = await strategy.compute_explicitly("- Definition of signals: ...", _env(db), [])

    assert result == {}


async def test_v2_compute_explicitly_degrades_to_empty_dict_on_malformed_json(db):
    strategy = TurnProtocolUsingSchema(FakeAiServiceV2(metadata={"signals": "not valid json"}))

    result = await strategy.compute_explicitly("- Definition of signals: ...", _env(db), [])

    assert result == {}
