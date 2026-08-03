"""TurnStrategy: the one part of chat-turn processing that depends on
which "protocol" the active AI provider speaks (see ai.llm_provider.
supports_structured_metadata) — isolated behind this shared interface so
ChatService can hold either concrete strategy (see turn_strategy_v1.py/
turn_strategy_v2.py) interchangeably, chosen fresh every turn by
chat.turn_strategy_builder.build_turn_strategy (a separate responsibility
on purpose — see its own module docstring). Everything else about
processing a turn (session/db bookkeeping, auto-tracking, building the
turn's own response) is genuinely identical either way and stays in
ChatService itself (see its own _begin_turn/_finish_turn) — only "how do
I actually get the reply text plus its audio/signals/env" differs, which
is exactly what generate_reply below covers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ai.ai_service import AiService, OnRetry
from chat.env import Env
from chat.turn_callbacks import OnChunk, OnMetadata


class TurnStrategy(ABC):
    def __init__(self, ai_service: AiService) -> None:
        self._ai_service = ai_service

    @abstractmethod
    async def generate_reply(
        self,
        base_prompt: str,
        signal_definition: str | None,
        env: Env,
        chat_history: list[dict],
        on_retry: OnRetry | None,
        on_chunk: OnChunk | None,
        on_metadata: OnMetadata | None,
    ) -> tuple[str, str | None, dict | None, dict]:
        """Returns (reply, audio_text, signal_values, env_updates) for
        this one AI call — `chat_history` is already fully built (see
        TurnProcessor._generate_reply), so a concrete strategy's own job
        is twofold: assemble the actual system prompt from `base_prompt`
        plus its own dialect's metadata section (built from
        `signal_definition`/`env` — see ChatService._build_turn_prompt_parts
        for why that section can't be pre-built the same way for every
        provider; `signal_definition` is None exactly when none is needed
        at all, e.g. a fixed_message translation turn — use `base_prompt`
        unchanged in that case), then decide *how* to ask the provider for
        a reply, streamed (on_chunk given) or not, and how to recover its
        own audio/signal-values/env alongside it. Never raises for a
        malformed reply — a missing/broken tag or metadata key just
        degrades to that piece being absent (None/{})."""
        raise NotImplementedError

    @abstractmethod
    async def compute_explicitly(
        self, signal_definition: str, env: Env, call_history: list[dict],
    ) -> dict[str, Any]:
        """Makes a dedicated call purely to recover raw signal values —
        no chat reply, no `base_prompt`, no automaton state involved —
        used by tracking.auto_tracker.AutoTracker.run's own explicit-
        fallback branch when there's no already-generated reply to
        piggyback signals off of (either the embedded report came back
        empty, or auto-tracking needs to run before the AI has replied
        at all this turn, see autotracking_on_user_message). Each
        concrete strategy does this in its own dialect, same as
        generate_reply above (tags for v1, on_metadata for v2). Returns
        the raw {name: value} dict, unvalidated — coercing it against
        the automaton's own declared signals is still the caller's own
        job (see tracking.evaluator.SignalEvaluator.validate). Never
        raises: an AI-service failure or malformed report both degrade
        to {}."""
        raise NotImplementedError
