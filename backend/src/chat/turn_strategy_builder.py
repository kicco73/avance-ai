"""The one place that decides which TurnStrategy a turn should use (see
turn_strategy.py's own module docstring) — a separate responsibility from
either concrete strategy or from ChatService itself, so the decision (today:
a static capability check against whichever provider is currently active,
see ai.ai_service.AiService.supports_metadata_generate/
supports_metadata_stream) has one seam to extend later without touching
ChatService or either strategy's own implementation.
"""
from __future__ import annotations

import logging

from ai.ai_service import AiService
from chat.turn_strategy import TurnStrategy
from chat.turn_strategy_v1 import TurnStrategyV1
from chat.turn_strategy_v2 import TurnStrategyV2

logger = logging.getLogger(__name__)


def build_turn_strategy(ai_service: AiService, *, wants_streaming: bool) -> TurnStrategy:
    """`wants_streaming` is the caller's own choice (on_chunk given or
    not — see ChatService.process_turn), not something this second-
    guesses: a plain blocking HTTP caller (controller.py's POST /api/chat/
    messages) and the streaming websocket one (ws_adapter.py) both
    deserve the richest metadata handling available for the call shape
    they actually asked for, but switching a blocking caller over to a
    streamed-then-buffered call under the hood just to chase a provider
    that only streams would be a bigger, riskier change than this
    decision is meant to make on its own.

    Checked fresh on every turn, never cached: the active provider can
    change between two turns (see AiService.select_model), and a provider
    that could support on_metadata in principle but hasn't actually
    implemented it yet for a given config would still need to fall back
    correctly the moment it's selected."""
    supports_metadata = (
        ai_service.supports_metadata_stream() if wants_streaming
        else ai_service.supports_metadata_generate()
    )
    strategy_name = "TurnStrategyV2" if supports_metadata else "TurnStrategyV1"
    logger.info(
        "Turn strategy: %s (streaming=%s).",
        strategy_name,
        wants_streaming,
    )
    return TurnStrategyV2(ai_service) if supports_metadata else TurnStrategyV1(ai_service)
