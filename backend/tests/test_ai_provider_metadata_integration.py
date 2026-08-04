from __future__ import annotations

import pytest

from ai.ai_service import AiService
from chat.turn_protocol_using_text_extraction import TurnProcotolUsingTextExtraction
from chat.turn_protocol_using_schema import TurnProtocolUsingSchema
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
    """A throwaway Env-shaped object — each TurnStrategy's own metadata-
    prompt builder only ever reads to_dict() off of whatever it's given,
    and a real chat.env.Env needs a real Db/session, more than this test
    needs just to build a system prompt."""

    def to_dict(self) -> dict:
        return {}


BASE_PROMPT = "You are a helpful assistant."


def _history() -> list[dict]:
    return [{"role": "user", "content": PROMPT}]


def _classify(ai_service: AiService) -> str:
    return "v2" if ai_service.supports_schema() else "v1"


def _strategy_for(wanted: str):
    """Pins a fresh AiService (see AiService.select_model) to the first
    configured provider whose capability matches `wanted`, skipping this
    test outright if the local config has none — e.g. a .config.yml with
    only a v2 provider configured has nothing to run the "v1" tests
    against, and that's a skip, not a failure."""
    ai_service = AiService.from_config(_APP_CONFIG.ai_services)
    for index in range(len(_APP_CONFIG.ai_services)):
        ai_service.select_model(index)
        if _classify(ai_service) == wanted:
            return STRATEGY_FOR[wanted](ai_service)
    pytest.skip(
        f"No configured provider is '{wanted}' (see backend/.config.yml's ai-service.providers)."
    )


async def _run(wanted: str, *, streaming: bool):
    strategy = _strategy_for(wanted)
    chunks: list[str] = []
    live_metadata: dict[str, object] = {}

    async def on_chunk(chunk: str) -> None:
        chunks.append(chunk)

    async def on_metadata(key: str, value) -> None:
        live_metadata[key] = value

    reply, audio_text, signal_values, env_updates = await strategy.generate_reply(
        BASE_PROMPT, SIGNAL_DEFINITION, _StubEnv(), _history(), None, on_chunk if streaming else None, on_metadata
    )
    return reply, audio_text, signal_values, env_updates, chunks, live_metadata


def _assert_extracted_metadata(reply, audio_text, signal_values, env_updates) -> None:
    # No leftover tag markup either way — TurnStrategyV1 strips its own
    # [audio]/[signals]/[env] tags via ConcatTagFilter, TurnStrategyV2's
    # provider never embeds them in the visible text to begin with.
    assert reply.strip()
    for marker in ("[audio]", "[/audio]", "[signals]", "[/signals]", "[env]", "[/env]"):
        assert marker not in reply

    assert audio_text
    assert isinstance(audio_text, str)

    assert signal_values is not None
    assert isinstance(signal_values.get("mood"), (int, float))

    assert isinstance(env_updates, dict)


@pytest.mark.parametrize("wanted", ["v1", "v2"])
async def test_generate_extracts_metadata(wanted):
    """Blocking call (on_chunk=None) — TurnStrategyV1.generate_reply's own
    non-streaming branch (AiService.generate), or TurnStrategyV2's."""
    reply, audio_text, signal_values, env_updates, chunks, live_metadata = await _run(wanted, streaming=False)

    _assert_extracted_metadata(reply, audio_text, signal_values, env_updates)
    # Never streamed — on_chunk was never given, so nothing to have collected.
    assert chunks == []


@pytest.mark.parametrize("wanted", ["v1", "v2"])
async def test_generate_stream_extracts_metadata(wanted):
    """Streaming call (on_chunk given) — TurnStrategyV1's own
    _receive_ai_stream_and_sendreply, or TurnStrategyV2's generate_stream
    branch. Also checks the streamed chunks reassemble into the exact
    same visible reply, and that "audio" (the one key ever forwarded live
    — see TurnStrategyV1/V2's own on_metadata handling) actually arrived
    through on_metadata during the call, not just in the final return
    value."""
    reply, audio_text, signal_values, env_updates, chunks, live_metadata = await _run(wanted, streaming=True)

    _assert_extracted_metadata(reply, audio_text, signal_values, env_updates)
    assert chunks, "generate_stream produced no chunks at all"
    assert "".join(chunks) == reply
    assert live_metadata.get("audio") == audio_text
