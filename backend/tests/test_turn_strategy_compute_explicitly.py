"""Tests how signal (and other) metadata is extracted as a side effect of
TurnProtocol.generate_reply's on_metadata callback — a direct
on_metadata callback from AiService.generate_stream_with_metadata.
"""
from __future__ import annotations

import pytest

from tracking.fixed_project_context import FixedProjectContext
from tracking.env import PersistedEnv
from tracking.turn_protocol_using_schema import TurnProtocolUsingSchema

USERNAME = "user"
PROJECT_ID = "proj"

pytestmark = pytest.mark.contract


def _env(db) -> PersistedEnv:
    return PersistedEnv(db, FixedProjectContext(project_id=PROJECT_ID))


class FakeAiServiceV2:
    """Shaped like the current ai.ai_service.AiService.
    generate_stream_with_metadata: calls on_metadata once per configured
    key with its raw (string) value, then yields whatever "text" it has."""

    def __init__(self, metadata: dict | None = None, error: Exception | None = None) -> None:
        self._metadata = metadata or {}
        self._error = error

    async def generate_stream_with_metadata(self, system_prompt, history, on_metadata, schema):
        if self._error is not None:
            raise self._error
        for key, value in self._metadata.items():
            on_metadata(key, value)
        yield ""


async def _collect(protocol, db):
    metadata: dict = {}

    def on_metadata(key, value):
        metadata[key] = value

    chunks = []
    async for chunk in protocol.generate_reply(
        "base prompt", "- Definition of signals: ...", _env(db), [], on_metadata
    ):
        chunks.append(chunk)
    return "".join(chunks), metadata


async def test_v2_generate_reply_reports_the_raw_signals_field(db):
    protocol = TurnProtocolUsingSchema(
        FakeAiServiceV2(metadata={"signals": '{"mood": 75}'}), evaluate_signals_first=True
    )

    _, metadata = await _collect(protocol, db)

    assert metadata.get("signals") == '{"mood": 75}'


async def test_v2_generate_reply_also_reports_audio_and_env_under_their_own_keys(db):
    # TurnProtocolUsingSchema forwards every non-"text" key on_metadata
    # reports, unfiltered.
    protocol = TurnProtocolUsingSchema(
        FakeAiServiceV2(metadata={"audio": "hi", "env": "x: y", "signals": '{"mood": 1}'}),
        evaluate_signals_first=True,
    )

    _, metadata = await _collect(protocol, db)

    assert metadata == {"audio": "hi", "env": "x: y", "signals": '{"mood": 1}'}


async def test_v2_generate_reply_with_no_signals_field_reports_nothing(db):
    protocol = TurnProtocolUsingSchema(FakeAiServiceV2(metadata={"audio": "hi"}), evaluate_signals_first=True)

    _, metadata = await _collect(protocol, db)

    assert "signals" not in metadata
