"""The one place that decides which TurnStrategy a turn should use (see
turn_strategy.py's own module docstring) — a separate responsibility from
either concrete strategy or from ChatService itself, so the decision
(today: a single capability check against whichever provider is
currently active, see ai.ai_service.AiService.supports_metadata) has one
seam to extend later without touching ChatService or either strategy's
own implementation.
"""
from __future__ import annotations

import logging

from ai.ai_service import AiService
from chat.turn_strategy import TurnStrategy
from chat.turn_strategy_v1 import TurnStrategyV1
from chat.turn_strategy_v2 import TurnStrategyV2

logger = logging.getLogger(__name__)


def build_turn_strategy(ai_service: AiService, *, wants_streaming: bool) -> TurnStrategy:
    """`wants_streaming` no longer affects *which* strategy is chosen —
    see AiService.supports_metadata/ai.llm_provider.
    supports_structured_metadata's own docstrings for why a single,
    build_schema-based check now covers generate() and generate_stream()
    uniformly, unlike the two independent signature-inspection checks
    this replaced. Still taken as a parameter purely for this function's
    own log line below: a caller (see ChatService.process_turn) already
    knows it for free (on_chunk given or not), and it's useful telemetry
    for which of TurnStrategyV1/V2's own two call shapes a turn actually
    took.

    Checked fresh on every turn, never cached: the active provider can
    change between two turns (see AiService.select_model), and a provider
    that could support structured metadata in principle but hasn't
    actually had its own build_schema wired up yet (see AiService.
    _build_provider) would still need to fall back correctly the moment
    it's selected."""
    supports_metadata = ai_service.supports_metadata()
    strategy_name = "TurnStrategyV2" if supports_metadata else "TurnStrategyV1"
    logger.info(
        "Turn strategy: %s (streaming=%s).",
        strategy_name,
        wants_streaming,
    )
    return TurnStrategyV2(ai_service) if supports_metadata else TurnStrategyV1(ai_service)
