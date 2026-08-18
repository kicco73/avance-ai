"""OLD: TurnStrategyV1/V2 each had their own dedicated `compute_explicitly`
call — a "no reply to piggyback signals on" fallback used by tracking.
auto_tracker.AutoTracker.run's own explicit-fallback branch, each
recovering raw signal values in its own dialect (tags for v1, on_metadata
for v2).

CURRENT: neither `compute_explicitly` nor `AutoTracker` exist anywhere in
the source anymore (tracking/auto_tracker.py was deleted this refactor —
ground truth table row #5; `compute_explicitly` has no definition left
in tracking/turn_protocol*.py either — only stale docstring mentions in
tracking/definitions.py and tracking/evaluator.py). Signal extraction is
no longer a separate, dedicated call at all: TrackingProcessorAfterUserMessage/
AfterAiMessage (tracking/tracking_processor_user.py / _ai.py) always
extract signals as a side effect of the one real reply-generating call,
via TurnProtocol.generate_reply's own on_metadata callback (tracking/
turn_protocol.py) — tested directly here instead, using fakes shaped
like the CURRENT ai_service interface (generate_stream for v1/text-
extraction, generate_stream_with_metadata for v2/schema), rather than
compute_explicitly's old, now-nonexistent one.

Note on a real bug this surfaces: TurnProcotolUsingTextExtraction (v1)
drives its own on_metadata through tracking/text_filter.py's
StreamingTagFilter, which always wraps a given on_tag callback in
`asyncio.create_task(...)` — that requires a coroutine, but
turn_protocol_using_text_extraction.py:51 wires up a plain sync lambda,
so any v1 tag actually closing currently crashes with
`TypeError: a coroutine was expected`. On top of that, the dict
comprehension building those per-tag lambdas (turn_protocol_using_text_
extraction.py:51) closes over the same loop variable for every tag
(classic late-binding), so even without that crash every tag would be
misreported under the last tag's own name. The v1 tests below are
written against the INTENDED contract (each tag reported under its own
name); left as-is per this refactor's own ground rules — not this file's
job to fix backend/src/.
"""
from __future__ import annotations

import pytest

from tracking.env import PersistedEnv
from tracking.turn_protocol_using_text_extraction import TurnProcotolUsingTextExtraction
from tracking.turn_protocol_using_schema import TurnProtocolUsingSchema

USERNAME = "user"
PROJECT_NAME = "proj"

# Every test here is about the interface/dialect each TurnProtocol
# subclass reports raw signal (and other) metadata through — response
# shape, not a one-off behavioral fact.
pytestmark = pytest.mark.contract


def _env(db) -> PersistedEnv:
    return PersistedEnv(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)


class FakeAiServiceV1:
    """Shaped like the current ai.ai_service.AiService.generate_stream:
    a plain async generator of text chunks, no metadata callback of its
    own — v1's own metadata comes entirely from tag-scanning the text
    (see tracking/text_filter.py)."""

    def __init__(self, reply: str | None = None, error: Exception | None = None) -> None:
        self._reply = reply
        self._error = error

    async def generate_stream(self, system_prompt, history):
        if self._error is not None:
            raise self._error
        yield self._reply


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


async def test_v1_generate_reply_extracts_signals_from_a_signals_tag(db):
    protocol = TurnProcotolUsingTextExtraction(
        FakeAiServiceV1(reply='Hi![signals]{"mood": 75}[/signals]'), evaluate_signals_first=True
    )

    _, metadata = await _collect(protocol, db)

    assert metadata.get("signals") == '{"mood": 75}'


async def test_v1_generate_reply_with_no_signals_tag_reports_nothing(db):
    protocol = TurnProcotolUsingTextExtraction(
        FakeAiServiceV1(reply="just a plain reply, no tags"), evaluate_signals_first=True
    )

    reply, metadata = await _collect(protocol, db)

    assert reply == "just a plain reply, no tags"
    assert "signals" not in metadata


async def test_v2_generate_reply_reports_the_raw_signals_field(db):
    protocol = TurnProtocolUsingSchema(
        FakeAiServiceV2(metadata={"signals": '{"mood": 75}'}), evaluate_signals_first=True
    )

    _, metadata = await _collect(protocol, db)

    assert metadata.get("signals") == '{"mood": 75}'


async def test_v2_generate_reply_also_reports_audio_and_env_under_their_own_keys(db):
    # OLD contract: compute_explicitly deliberately filtered v2's own
    # on_metadata callback down to just "signals", ignoring "audio"/"env".
    # NEW contract: TurnProtocolUsingSchema._generate_reply (tracking/
    # turn_protocol_using_schema.py:67-76) just delegates straight to
    # AiService.generate_stream_with_metadata and forwards every
    # non-"text" key it reports, unfiltered — that selective ignoring was
    # compute_explicitly's own logic, which no longer exists anywhere.
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


# Deleted (regression, invalid — OLD behavior no longer exists):
#
# - test_v1_compute_explicitly_degrades_to_empty_dict_on_ai_failure
# - test_v2_compute_explicitly_degrades_to_empty_dict_on_ai_failure
#   compute_explicitly used to catch an AI-provider failure and degrade
#   to {}. Nothing in the current call chain does that anymore: neither
#   tracking/turn_protocol.py's generate_reply nor tracking/turn_protocol_
#   using_text_extraction.py:49-60/turn_protocol_using_schema.py:67-76
#   wrap the underlying ai_service call in any try/except — an
#   AIServiceError now propagates all the way up to the centralized
#   FastAPI handler instead (backend/src/error_handlers.py:41,58's
#   ai_service_error_handler). There's no "degrades to empty dict" fact
#   left to verify at this layer.
#
# - test_v2_compute_explicitly_degrades_to_empty_dict_on_malformed_json
#   The malformed-JSON tolerance this checked lives entirely inside
#   ai.ai_service.AiService.generate_stream_with_metadata's own
#   partial_json_parser handling (ai/ai_service.py:122-168) now — not in
#   tracking/turn_protocol_using_schema.py at all, which just builds a
#   schema dict and delegates verbatim. A fake exercising only the
#   TurnProtocol layer (as this file does) can't reach that logic; it
#   belongs in an ai/ai_service.py-focused test file instead, outside
#   this batch's own tracking/chat-turn layer.
