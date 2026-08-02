"""Turns a model reply into validated signal values — the one place both
of AutoTracker's two ways of getting them funnel through, sharing the
exact same prompt/tag convention and validation:
  - `validate(automaton, raw_values)`: the reply was already generated
    for some other reason (a normal chat turn) and already carries an
    [avance] tag (see chat.metadata_handler.MetadataHandler) — the caller
    (AutoTracker.run) has already pulled the raw values out via
    MetadataHandler.signal_values; this just coerces them against the
    automaton's own declared signals.
  - `compute_explicitly(...)`: no reply to piggyback on at all — either
    the embedded one came back empty, or auto-tracking needs to run
    before the AI has replied at all this turn (autotracking_on_user_
    message). Makes its own dedicated call using the exact same prompt/
    tag convention as a normal turn (MetadataHandler.build_prompt), then
    validates through the same path.
Replaces Signals.compute()'s own, now-deprecated standalone prompt
(SIGNALS_SYSTEM_PROMPT_TEMPLATE asked for bare JSON, a second format to
maintain and parse) — Signals itself keeps only signal definitions and
payload-building, nothing AI-call-shaped.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from automaton.automaton import Automaton
from ai.ai_service import AiService
from ai.llm_provider import AIServiceError
from chat.env import Env
from chat.metadata_handler import MetadataHandler
from chat.signals import Signals

logger = logging.getLogger(__name__)

# The shape compute_explicitly() needs to build a priming turn from a
# list of automaton.MemoryArchive — supplied by the caller, same as
# Signals' own former compute() took it.
BuildPrimingMessages = Callable[[list], list[dict]]


class SignalEvaluator(object):
    def __init__(self, metadata_handler: MetadataHandler) -> None:
        self._metadata_handler = metadata_handler

    @staticmethod
    def _validate_one(raw_value: object) -> int | float | None:
        # Signals are unconstrained numbers, int or float: no fixed range
        # (a signal's own `definition` prompt is free to ask for e.g.
        # 0-100, but the software itself doesn't enforce it).
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            return None
        return raw_value

    def validate(
        self, automaton: Automaton, raw_values: dict | None, names: set[str] | None = None
    ) -> dict[str, int | float | None]:
        """Coerces whatever was reported (embedded or explicit) against
        `automaton`'s own declared signals: exactly one entry per
        declared signal in the result (or, when `names` is given — see
        Automaton.triggerable_signal_names — per signal in that subset
        only, the current state's own auto-tracking scope), unknown/
        malformed values become None, anything extra in `raw_values` is
        dropped. Omitted `names` means every declared signal, unchanged
        from before this parameter existed."""
        raw_values = raw_values or {}
        relevant = automaton.signals if names is None else [s for s in automaton.signals if s.name in names]
        return {s.name: self._validate_one(raw_values.get(s.name)) for s in relevant}

    async def compute_explicitly(
        self,
        ai_service: AiService,
        signals: Signals,
        env: Env,
        build_priming_messages: BuildPrimingMessages,
        session_id: int,
        pending_message: dict | None = None,
        since: datetime | None = None,
        names: set[str] | None = None,
    ) -> dict[str, int | float | None]:
        """No reply to piggyback on — makes its own dedicated call, using
        the exact same system prompt (MetadataHandler.build_prompt, the
        same one a normal turn gets) and the exact same [avance]-tag
        extraction (MetadataHandler._filter_text_and_extract_tags) as the
        embedded path, then validates through the same validate() above.
        `names` (see Automaton.triggerable_signal_names) scopes the
        prompt's own signal definitions, this call's own signal
        attachments, and validate()'s result down to only the signals
        the current state's own outgoing triggers could actually use —
        omitted means every declared signal. Any AI-service failure
        degrades to every (scoped) signal reported None, same as a
        malformed/empty embedded report would."""
        automaton = signals.automaton
        system_prompt = self._metadata_handler.build_prompt(signals, env, names)
        relevant_signals = automaton.signals if names is None else [s for s in automaton.signals if s.name in names]
        # Each signal brings only its own attachments into this call —
        # never a state's or general_prompt's (different scope entirely).
        signal_attachments = [a for s in relevant_signals for a in s.attachments.values()]
        priming_messages = build_priming_messages(signal_attachments)
        call_history = priming_messages + signals.history_window(session_id, pending_message, since)

        try:
            raw_reply = await ai_service.generate(system_prompt, call_history)
        except AIServiceError as exc:
            logger.error("Failed to compute signals explicitly: %s", exc)
            return self.validate(automaton, None, names)

        _, tags = self._metadata_handler._filter_text_and_extract_tags(raw_reply)
        # tags['signals'] is the raw parsed [avance] tag content (see
        # _filter_text_and_extract_tags) — signal_values() is what
        # actually drills into its own "signals" key, same as the
        # embedded path already does in ChatService._process_turn_locked.
        return self.validate(automaton, self._metadata_handler.signal_values(tags["signals"]), names)
