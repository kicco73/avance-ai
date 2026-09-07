"""The system prompt's own env block — the automaton's declared variables
the model is allowed to see, rendered as `key: value` lines. Its
perimeter is decided entirely here: the block exists for every state
whenever the project exports at least one env key (`ai-access: readonly`
— see Automaton.exported_env_keys), and truncates every value to
MAX_ENV_VALUE_CHARS. Anywhere else (nothing exported) the block simply
doesn't exist — not even empty. The model's own memory is a separate
block with its own heading (see TurnProtocol), never merged with this
one. Read-only, full stop: there is no model-facing write path for these
— an action's own `env:` script is the only thing that ever changes one."""
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
        """None — no block at all — only when the project exports no env
        key whatsoever. Every state gets the same block otherwise: env is
        project-global, not scoped per state. A key never set yet renders
        with an empty value, the same row a read would return."""
        exported = automaton.exported_env_keys()
        if not exported:
            return None
        current = env.action_set()
        return cls({env_key.name: current.get(env_key.name, "") for env_key in exported})

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
