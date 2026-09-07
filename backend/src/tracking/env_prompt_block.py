"""The system prompt's own env block — this state's own `input` variables
(see automaton.State.input), rendered as `key: value` lines, truncated to
MAX_ENV_VALUE_CHARS. A state that declares no `input` gets no block at
all — not even empty. The model's own memory is a separate block with its
own heading (see TurnProtocol), never merged with this one. Read-only,
full stop: there is no model-facing write path for these — an action's
own `env:` script (or this same state's own `output`, copied back once
the turn completes — see TrackingProcessor.process) is what changes one."""
from __future__ import annotations

from typing import Any

from automaton.automaton import Automaton, State
from tracking.env import Env

MAX_ENV_VALUE_CHARS = 200

ENV_BLOCK_HEADER = (
    "Current environment — the automaton's own variables (name: value). Read-only: never write these in "
    "the `memory` field."
)


class EnvPromptBlock:
    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    @classmethod
    def for_state(cls, env: Env, automaton: Automaton, state: State) -> "EnvPromptBlock | None":
        """None — no block at all — only when `state` declares no `input`.
        A key never set yet renders with an empty value, the same row a
        read would return."""
        if not state.input:
            return None
        current = env.action_set()
        return cls({name: current.get(name, "") for name in state.input})

    @staticmethod
    def _render_value(value: Any) -> str:
        text = "" if value is None else str(value)
        if len(text) <= MAX_ENV_VALUE_CHARS:
            return text
        return f"{text[:MAX_ENV_VALUE_CHARS]}[response too long — provide more specific filters via a select_rows_* read]"

    def lines(self) -> dict[str, str]:
        """key -> rendered (already truncated) value, in declaration order
        — what text() joins, exposed for the per-key token estimate (see
        tracking.turn_size_estimate)."""
        return {key: self._render_value(value) for key, value in self._values.items()}

    def text(self) -> str:
        body = "\n".join(f"{key}: {value}" for key, value in self.lines().items())
        return f"{ENV_BLOCK_HEADER}\n{body}"
