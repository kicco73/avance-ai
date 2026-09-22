"""The system prompt's own env block — this state's own `input` variables
(see automaton.State.input), rendered as `key: value` lines in full, each
followed by the key's own `ai_definition` (see automaton.EnvKey) indented
beneath it. A state that declares no `input` gets no block at all — not
even empty. The model's own memory is a separate block with its own
heading (see TurnProtocol), never merged with this one. Read-only, full
stop: there is no model-facing write path for these — an action's own
`env:` script (or this same state's own `output`, copied back once the
turn completes — see TrackingProcessor.process) is what changes one."""
from __future__ import annotations

import json
from typing import Any, Iterable

from automaton.automaton import Automaton, State
from tracking.env import Env


ENV_BLOCK_HEADER = (
    "Current environment — the automaton's own variables (name: value). Read-only: never write these in "
    "the `memory` field."
)


class EnvPromptBlock:
    def __init__(self, values: dict[str, Any], definitions: dict[str, str | None]) -> None:
        self._values = values
        self._definitions = definitions

    @classmethod
    def for_state(cls, env: Env, automaton: Automaton, state: State) -> "EnvPromptBlock | None":
        """None — no block at all — only when `state` declares no `input`.
        A key never set yet renders with an empty value, the same row a
        read would return."""
        if not state.input:
            return None
        return cls._for_names(env, automaton, state.input)

    @classmethod
    def for_states(cls, env: Env, automaton: Automaton, states: Iterable[State]) -> "EnvPromptBlock | None":
        """Same as for_state, but the union of every given state's own
        `input` list (declaration order, deduplicated) — for a caller that
        evaluates a group of turns from one snapshot without resolving
        each turn's own individual state first."""
        input_names = dict.fromkeys(name for state in states for name in state.input)
        if not input_names:
            return None
        return cls._for_names(env, automaton, input_names)

    @classmethod
    def _for_names(cls, env: Env, automaton: Automaton, names: Iterable[str]) -> "EnvPromptBlock":
        current = env.action_set()
        definitions = {env_key.name: env_key.ai_definition for env_key in automaton.env_keys}
        return cls(
            {name: current.get(name, "") for name in names},
            {name: definitions.get(name) for name in names},
        )

    @staticmethod
    def _render_value(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else "" if value is None else str(value)

    def _render_key(self, key: str) -> str:
        line = f"{key}: {self._render_value(self._values[key])}"
        definition = self._definitions.get(key)
        return f"{line}\n\t{definition}" if definition else line

    def lines(self) -> dict[str, str]:
        """key -> that key's own rendered text (`key: value`, already
        truncated, plus its definition), in declaration order — what
        text() joins, exposed for the per-key token estimate (see
        tracking.turn_size_estimate)."""
        return {key: self._render_key(key) for key in self._values}

    def text(self) -> str:
        return f"{ENV_BLOCK_HEADER}\n" + "\n".join(self.lines().values())
