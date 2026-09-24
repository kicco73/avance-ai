from __future__ import annotations

import pytest

from ai import AiService
from ai import config as ai_config
from ai.turn.prompt import Prompt, TextPrompt
from ai.turn.turn_protocol_using_schema import TurnProtocolUsingSchema
from config import AppConfig, ConfigError

try:
    _APP_CONFIG = AppConfig()
    _AI_SERVICES = [s for s in ai_config.parse(_APP_CONFIG.raw, _APP_CONFIG.path) if "live" in s.modes]
except ConfigError:
    _AI_SERVICES = []

pytestmark = pytest.mark.skipif(
    not _AI_SERVICES,
    reason="No usable backend/.config.yml ai-service config found — these hit real provider APIs.",
)

GREETER = "You are the receptionist of a hotel. Greet the guest in one short sentence."


async def _reply(provider_index: int, history: list[dict]) -> str:
    ai_service = AiService.for_live(_AI_SERVICES)
    ai_service.select_model(provider_index)
    chunks = [
        chunk async for chunk in TurnProtocolUsingSchema(ai_service).generate_reply(
            Prompt.chain(TextPrompt(GREETER)), history, lambda key, value: None,
        )
    ]
    return "".join(chunks)


@pytest.mark.contract
@pytest.mark.parametrize("provider_index", range(len(_AI_SERVICES)), ids=[s.model for s in _AI_SERVICES])
async def test_every_provider_speaks_first_on_an_empty_conversation(provider_index):
    assert (await _reply(provider_index, [])).strip()


@pytest.mark.contract
@pytest.mark.parametrize("provider_index", range(len(_AI_SERVICES)), ids=[s.model for s in _AI_SERVICES])
async def test_every_provider_speaks_again_after_its_own_last_message(provider_index):
    history = [
        {"role": "user", "content": "Hi, I have a booking."},
        {"role": "assistant", "content": "Welcome! Let me look it up."},
    ]
    assert (await _reply(provider_index, history)).strip()
