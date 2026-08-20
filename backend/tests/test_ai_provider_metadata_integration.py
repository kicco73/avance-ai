from __future__ import annotations

import pytest

from ai.ai_service import AiService
from tracking.turn_protocol_using_text_extraction import TurnProcotolUsingTextExtraction
from tracking.turn_protocol_using_schema import TurnProtocolUsingSchema
from config import AppConfig, ConfigError

try:
    _APP_CONFIG = AppConfig()
    _CONFIG_ERROR: str | None = None
except ConfigError as exc:
    _APP_CONFIG = None
    _CONFIG_ERROR = str(exc)

pytestmark = pytest.mark.skipif(
    _APP_CONFIG is None or not _APP_CONFIG.ai_services,
    reason=(
        "No usable backend/.config.yml ai-service config found "
        f"({_CONFIG_ERROR or 'no providers configured'}) — these hit real "
        "provider APIs and need real, locally-configured credentials."
    ),
)

SIGNAL_DEFINITION = "- mood (a number from 0 to 100): how happy the user's own message sounds."

PROMPT = (
    'The user just said: "I got promoted today, I\'m overjoyed!". '
    "Report mood as exactly 95, and store today's good news under the env key "
    "'last_win' with the value 'promotion'."
)

STRATEGY_FOR = {"v1": TurnProcotolUsingTextExtraction, "v2": TurnProtocolUsingSchema}


class _StubEnv:
    """A throwaway Env-shaped object — TurnProtocol.generate_reply only
    reads serialise_as_text() off whatever it's given, sparing this test
    a real Db/session-backed Env."""

    def serialise_as_text(self) -> str:
        return ""


BASE_PROMPT = "You are a helpful assistant."


def _history() -> list[dict]:
    return [{"role": "user", "content": PROMPT}]


def _classify(ai_service: AiService) -> str:
    return "v2" if ai_service.is_provider_with_schema() else "v1"


def _strategy_for(wanted: str):
    """Pins a fresh AiService to the first configured provider whose
    capability matches `wanted`, skipping the test if the local config
    has none matching."""
    ai_service = AiService.from_config(_APP_CONFIG.ai_services)
    for index in range(len(_APP_CONFIG.ai_services)):
        ai_service.select_model(index)
        if _classify(ai_service) == wanted:
            # True picks one of the two valid tag/field orderings,
            # irrelevant to what this test actually checks.
            return STRATEGY_FOR[wanted](ai_service, True)
    pytest.skip(
        f"No configured provider is '{wanted}' (see backend/.config.yml's ai-service.providers)."
    )


async def _run(wanted: str):
    strategy = _strategy_for(wanted)
    chunks: list[str] = []
    live_metadata: dict[str, object] = {}

    def on_metadata(key: str, value) -> None:
        # Called sync, fire-and-forget — never awaited by a provider.
        live_metadata[key] = value

    async for chunk in strategy.generate_reply(BASE_PROMPT, SIGNAL_DEFINITION, _StubEnv(), _history(), on_metadata):
        chunks.append(chunk)

    return "".join(chunks), chunks, live_metadata


def _assert_extracted_metadata(reply, live_metadata) -> None:
    # No leftover tag markup either way: the text-extraction strategy
    # strips its own [audio]/[signals]/[env] tags, and the schema
    # strategy never embeds them in the visible text to begin with.
    assert reply.strip()
    for marker in ("[audio]", "[/audio]", "[signals]", "[/signals]", "[env]", "[/env]"):
        assert marker not in reply

    assert isinstance(live_metadata.get("audio"), str) and live_metadata["audio"]

    # "signals" arrives through on_metadata as a raw, still-JSON-encoded
    # string; a layer above (TrackingProcessor) turns it into a dict.
    assert isinstance(live_metadata.get("signals"), str)
    assert "mood" in live_metadata["signals"]


@pytest.mark.contract
@pytest.mark.parametrize("wanted", ["v1", "v2"])
async def test_generate_reply_streams_chunks_and_reports_metadata(wanted):
    """TurnProtocol.generate_reply always returns an AsyncIterator[str] of
    visible text chunks, with metadata delivered live through the
    on_metadata callback's raw string values, never a return tuple."""
    reply, chunks, live_metadata = await _run(wanted)

    _assert_extracted_metadata(reply, live_metadata)
    assert chunks, "generate_reply produced no chunks at all"
    assert "".join(chunks) == reply
