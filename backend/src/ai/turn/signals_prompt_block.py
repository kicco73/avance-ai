from __future__ import annotations

from typing import Any

from automaton.automaton import Automaton, State

from .env_prompt_block import EnvPromptBlock


SIGNALS_BLOCK_HEADER = (
    "Current signals — what was last measured in this conversation (name: value), each with its definition. "
    "Read-only: you are not asked to measure them. None means never measured yet."
)


class SignalsPromptBlock:
    def __init__(self, values: dict[str, Any], definitions: dict[str, str]) -> None:
        self._values = values
        self._definitions = definitions

    @classmethod
    def for_state(cls, automaton: Automaton, state: State, snapshot: dict | None) -> "SignalsPromptBlock | None":
        names = automaton.read_signal_names(state.key)
        if not names:
            return None
        measured = snapshot or {}
        signals = [signal for signal in automaton.signals if signal.name in names]
        return cls(
            {signal.name: measured.get(signal.name) for signal in signals},
            {signal.name: signal.definition for signal in signals},
        )

    def lines(self) -> dict[str, str]:
        return {
            f"signal.{name}": f"{name}: {value}\n\t{self._definitions[name]}"
            for name, value in self._values.items()
        }

    def text(self) -> str:
        return f"{SIGNALS_BLOCK_HEADER}\n" + "\n".join(self.lines().values())


class InputBlocks:
    def __init__(self, blocks: list[EnvPromptBlock | SignalsPromptBlock]) -> None:
        self._blocks = blocks

    @classmethod
    def of(cls, *blocks: EnvPromptBlock | SignalsPromptBlock | None) -> "InputBlocks | None":
        present = [block for block in blocks if block is not None]
        return cls(present) if present else None

    def lines(self) -> dict[str, str]:
        return {key: text for block in self._blocks for key, text in block.lines().items()}

    def text(self) -> str:
        return "\n\n".join(block.text() for block in self._blocks)
