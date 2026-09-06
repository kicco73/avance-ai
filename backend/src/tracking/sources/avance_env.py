"""The `avance:env` source driver — the project's own declared env keys,
exposed to the model as a single-row table whose columns are exactly the
keys with `ai-access` other than none (see automaton.EnvKey). Declared
like any other source (`url: avance:env`, no other parameter; ui-label/
ai-definition as usual) and listed per state like any other: a state
that puts it in ai-may/must-read-sources gets `select_rows_containing`
(and the prompt's own env block, see tracking.env_prompt_block), one that puts it in
ai-may-write-sources gets `update`. `value(key=...)` — one variable as a
scalar string — is never a tool: scripts/triggers only. This is the model's *only* channel
for writing an automaton variable: `update(fields=...)` writes the
readwrite keys into the session's own env through Env.update_action_set
— PersistedEnv for a live session, the ephemeral one for a test session,
exactly as an action's own `env:` script does — and refuses a readonly or
unexported key as error text, writing nothing. Scripts are never subject
to ai-access: it gates the model alone."""
from __future__ import annotations

import csv
import io

from automaton.automaton import EnvKey

from .base import SourceContext, SourceDriver

# The `url` path that selects this driver under the `avance` scheme (the
# rest of that scheme is AvanceArchiveSource's) — see driver_class_for.
PATH = "env"


class AvanceEnvSource(SourceDriver):
    SUPPORTED_METHODS = frozenset({"select_rows_containing", "update", "value"})
    METHOD_DESCRIPTIONS = {
        "select_rows_containing": (
            "The automaton's own variables as a one-row table: the header row names every variable "
            "you may see, the second row holds their current values. `values` is ignored: there is only "
            "one row — e.g. source.<name>.select_rows_containing()."
        ),
        "update": (
            "Sets one or more of the automaton's own variables — `fields` maps a variable name to its new "
            "value, e.g. source.<name>.update(fields={'pnr': 'ABC123'}). Only the variables listed in this "
            "tool's own schema can be written; the rest are read-only. `values` is ignored: there is only "
            "one row."
        ),
        "value": (
            "One variable's current value as a scalar string — e.g. source.<name>.value(key='pnr'). "
            "`values` is ignored: there is only one row. Scripts/triggers only, never a model tool."
        ),
    }

    def __init__(self, context: SourceContext, name: str, path: str) -> None:
        super().__init__(context, name, path)
        self._automaton = context.automaton
        self._env = context.env

    def _exported_keys(self) -> list[EnvKey]:
        return self._automaton.exported_env_keys()

    def _current_values(self) -> dict[str, str]:
        current = self._env.action_set()
        return {env_key.name: self._as_text(current.get(env_key.name, "")) for env_key in self._exported_keys()}

    @staticmethod
    def _as_text(value: object) -> str:
        return "" if value is None else str(value)

    def select_rows_containing(self, *values: str) -> str:
        current = self._current_values()
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(list(current))
        writer.writerow(list(current.values()))
        return self._bounded(out.getvalue())

    def value(self, *values: str, key: str) -> str:
        current = self._current_values()
        if key not in current:
            return f"error: unknown variable(s) {key!r} — available: {', '.join(current)}"
        return current[key]

    def update(self, *values: str, fields: dict[str, str], origin: str | None = None) -> str:
        """`origin`: the caller's own to set — ToolSet.call passes 'tool'
        when the model itself calls `update` through the tool-calling
        loop; a script/trigger calling this same method directly
        (source.<name>.update(fields=...)) leaves it at the default None,
        indistinguishable from an action's own `env:` write
        (TrackingEngine.apply_action_env writes with no origin either).
        Only ever load-bearing for binding a model-made write to the
        turn's own assistant message (see Db.link_tool_env_writes_to_message)."""
        if not fields:
            return "error: nothing to update — `fields` must name at least one variable."
        writable = {env_key.name for env_key in self._exported_keys() if env_key.writable}
        exported = {env_key.name for env_key in self._exported_keys()}
        rejected = [key for key in fields if key not in writable]
        if rejected:
            reasons = ", ".join(
                f"{key!r} is read-only" if key in exported else f"{key!r} is not a variable you can access"
                for key in rejected
            )
            return (
                f"error: {reasons} — nothing was written. Writable variable(s): "
                f"{', '.join(sorted(writable)) or 'none'}."
            )
        self._env.update_action_set({key: self._as_text(value) for key, value in fields.items()}, origin=origin)
        return "1 row updated"

    def parameter_schema(self, method: str) -> dict | None:
        exported = self._exported_keys()
        if method == "update":
            return {
                "type": "object",
                "properties": {
                    "values": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Ignored — this source has a single row. Pass an empty list.",
                    },
                    "fields": {
                        "type": "object",
                        "properties": {
                            env_key.name: {"type": "string", "description": env_key.ai_definition or ""}
                            for env_key in exported if env_key.writable
                        },
                        "additionalProperties": False,
                        "minProperties": 1,
                        "description": "Variable name → new value. Only the variables listed here can be written.",
                    },
                },
                "required": ["values", "fields"],
            }
        return None
