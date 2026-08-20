"""Coerces raw signal values (however they were actually obtained)
against the automaton's own declared signals. Provider-agnostic on
purpose: getting the raw values is the caller's job, so this module
never needs to know which method produced them."""
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
        """Exactly one entry per declared signal (or, when `names` is
        given, per signal in that subset only); unknown/malformed values
        become None, anything extra in `raw_values` is dropped."""
        raw_values = raw_values or {}
        relevant = automaton.signals if names is None else [s for s in automaton.signals if s.name in names]
        return {s.name: self._validate_one(raw_values.get(s.name)) for s in relevant}
