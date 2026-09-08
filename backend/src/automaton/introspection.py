"""IntrospectionMixin — questions about the project that no chat turn
asks: which signals are referenced anywhere (the Inspector's "relevant
signals" filter), whether a state's triggers mention a metric name (so
metrics can skip resolving a value set nothing reads), and the
name-returning form of trigger evaluation that only tests call.

A platform contract like payloads.py: a compiled product without a design
view or a metrics service composes neither. The data these read is the
core's own (see analysis.py); only the questions live here."""
from __future__ import annotations

from . import analysis


class IntrospectionMixin(object):

    def triggers_reference(self, state_key: str, names: set[str]) -> bool:
        """Whether any triggerable action leaving `state_key` references
        one of `names` as a *bare* identifier — in practice a metric
        name. Lets a caller skip resolving an expensive value set when nothing needs it."""
        cached = self._trigger_bare_names.get(state_key)
        if cached is None:
            cached = analysis.trigger_bare_names(self.states[state_key])
            self._trigger_bare_names[state_key] = cached
        return bool(cached & names)

    def all_triggerable_signal_names(self) -> set[str]:
        """triggerable_signal_names, unioned across every state — the
        project-wide "is this signal used anywhere" view (backs the
        Inspector Signals tab's "relevant signals" filter)."""
        return {name for state_key in self.states for name in self.triggerable_signal_names(state_key)}

    def evaluate_triggers(self, state_key: str, scope: dict[str, Any]) -> str | None:
        action = self.evaluate_triggers_action(state_key, scope)
        return action.name if action else None
