from __future__ import annotations

import pytest

from ai import AiService
from tracking.channels import AudioChannel, MemoryChannel, SignalsChannel, TextChannel
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
    "Report mood as exactly 95, and store today's good news under the memory key "
    "'last_win' with the value 'promotion'."
)

class _StubEnv:
    """A throwaway Env-shaped object — MemoryChannel only reads
    memory_as_text() off whatever it's given, sparing this test a real
    Db/session-backed Env."""

    def memory_as_text(self) -> str:
        return ""


BASE_PROMPT = "You are a helpful assistant."


def _history() -> list[dict]:
    return [{"role": "user", "content": PROMPT}]


def _protocol() -> TurnProtocolUsingSchema:
    """Pins a fresh AiService to its first configured provider."""
    ai_service = AiService.for_live(_APP_CONFIG.ai_services)
    ai_service.select_model(0)
    return TurnProtocolUsingSchema(ai_service)


async def _run():
    protocol = _protocol()
    channels = [SignalsChannel(SIGNAL_DEFINITION), AudioChannel(), TextChannel(BASE_PROMPT), MemoryChannel(_StubEnv())]
    chunks: list[str] = []
    live_metadata: dict[str, object] = {}

    def on_metadata(key: str, value) -> None:
        # Called sync, fire-and-forget — never awaited by a provider.
        live_metadata[key] = value

    async for chunk in protocol.generate_reply(channels, _history(), on_metadata):
        chunks.append(chunk)

    return "".join(chunks), chunks, live_metadata


def _assert_extracted_metadata(reply, live_metadata) -> None:
    # The schema protocol never embeds [audio]/[signals]/[memory] markup in
    # the visible text to begin with.
    assert reply.strip()
    for marker in ("[audio]", "[/audio]", "[signals]", "[/signals]", "[memory]", "[/memory]"):
        assert marker not in reply

    assert isinstance(live_metadata.get("audio"), str) and live_metadata["audio"]

    # "signals" arrives through on_metadata already decoded (see
    # SignalsChannel.decode, invoked centrally by TurnProtocolUsingSchema.
    # generate_reply) — a dict, never the raw JSON string the model itself wrote.
    assert isinstance(live_metadata.get("signals"), dict)
    assert "mood" in live_metadata["signals"]


@pytest.mark.contract
async def test_generate_reply_streams_chunks_and_reports_metadata():
    """TurnProtocolUsingSchema.generate_reply always returns an
    AsyncIterator[str] of visible text chunks, with metadata delivered
    live through the on_metadata callback's already-decoded values, never
    a return tuple."""
    reply, chunks, live_metadata = await _run()

    _assert_extracted_metadata(reply, live_metadata)
    assert chunks, "generate_reply produced no chunks at all"
    assert "".join(chunks) == reply
