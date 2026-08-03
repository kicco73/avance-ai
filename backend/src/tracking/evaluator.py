"""Coerces raw signal values (however they were actually obtained — an
already-generated reply's own embedded report, or a TurnStrategy's own
dedicated compute_explicitly call, see tracking.auto_tracker.
AutoTracker.run) against the automaton's own declared signals. Provider-
agnostic on purpose: *getting* the raw values is a TurnStrategy's own
job now (see chat.turn_strategy.TurnStrategy.compute_explicitly's own
docstring for why — v1/v2 speak different wire formats, tags vs.
on_metadata), so this module never needs to know which one produced
them, only how to validate the result either way.
Replaces Signals.compute()'s own, now-deprecated standalone prompt
(SIGNALS_SYSTEM_PROMPT_TEMPLATE asked for bare JSON, a second format to
maintain and parse) — Signals itself keeps only signal definitions and
payload-building, nothing AI-call-shaped.
"""
from __future__ import annotations

from automaton.automaton import Automaton


class SignalEvaluator(object):
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
