"""The system prompt's own env block — the automaton's declared variables
the model is allowed to see, rendered as `key: value` lines. Its
perimeter is decided entirely here: the block exists only for a state
that lists an `avance:env` source in ai-may-read-sources/
ai-must-read-sources (see Automaton.reads_env_source), holds only the
keys with `ai-access` other than none (Automaton.exported_env_keys), and
truncates every value to MAX_ENV_VALUE_CHARS with a pointer at the
`select` tool for the rest. Anywhere else the block simply doesn't exist
— not even empty. The model's own memory is a separate block with its
own heading (see TurnProtocol), never merged with this one."""
from __future__ import annotations

from typing import Any

from automaton.automaton import Automaton, State
from tracking.env import Env

MAX_ENV_VALUE_CHARS = 200

ENV_BLOCK_HEADER = (
    "Current environment — the automaton's own variables (name: value). Read-only here: to change one, "
    "call the env source's `update` tool; never write these in the `memory` field."
)


class EnvPromptBlock:
    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    @classmethod
    def for_state(cls, env: Env, automaton: Automaton, state: State) -> "EnvPromptBlock | None":
        """None — no block at all — unless `state` reads an avance:env
        source and the project exports at least one key (AutomatonBuilder
        already guarantees the latter whenever an avance:env source is
        declared). A key never set yet renders with an empty value, the
        same row `select` would return."""
        if not automaton.reads_env_source(state):
            return None
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
        return f"{text[:MAX_ENV_VALUE_CHARS]}[response too long — provide more specific filters via select]"

    def lines(self) -> dict[str, str]:
        """key -> rendered (already truncated) value, in declaration order
        — what text() joins, exposed for the per-key token estimate (see
        tracking.turn_size_estimate)."""
        return {key: self._render_value(value) for key, value in self._values.items()}

    def text(self) -> str:
        body = "\n".join(f"{key}: {value}" for key, value in self.lines().items())
        return f"{ENV_BLOCK_HEADER}\n{body}"
