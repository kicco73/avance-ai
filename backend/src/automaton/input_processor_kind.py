"""Which processor answers in a state, as far as a build can tell.

`ai` is the model: the state's prompt is read and its signals are
computed. `system` is the automaton alone: what an action reaching it
writes with `chat.write` is the reply. The kind answers those questions at
build time; what runs a turn is turn/input_processor.py, keyed the same way."""
from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from automaton.automaton import State

REQUIRED = (
    "State '{key}': 'input-processor' is required, 'ai' or 'system' — 'ai' answers through "
    "the model, 'system' answers with what on-exit writes through chat.write(...); got {value!r}."
)
PROMPT_REQUIRED = "State '{key}': 'contextual-prompt' is required for input-processor: ai."


class InputProcessorKind:
    name: str

    def chat_enabled(self, declared: bool) -> bool:
        raise NotImplementedError

    def check_prompt(self, key: str, contextual_prompt: str | None) -> None:
        raise NotImplementedError

    def model_visible_io(self, state: "State") -> tuple[tuple[str, tuple[str, ...]], ...]:
        raise NotImplementedError


class AiKind(InputProcessorKind):
    name = "ai"

    def chat_enabled(self, declared: bool) -> bool:
        return declared

    def check_prompt(self, key: str, contextual_prompt: str | None) -> None:
        for _ in filter(None, [contextual_prompt is None]):
            raise ValueError(PROMPT_REQUIRED.format(key=key))

    def model_visible_io(self, state: "State") -> tuple[tuple[str, tuple[str, ...]], ...]:
        return (("input", tuple(state.input)), ("output", tuple(state.output)))


class SystemKind(InputProcessorKind):
    name = "system"

    def chat_enabled(self, declared: bool) -> bool:
        return False

    def check_prompt(self, key: str, contextual_prompt: str | None) -> None:
        return None

    def model_visible_io(self, state: "State") -> tuple[tuple[str, tuple[str, ...]], ...]:
        return ()


INPUT_PROCESSOR_KINDS: dict[str, InputProcessorKind] = {kind.name: kind for kind in (AiKind(), SystemKind())}


def kind_of(key: str, value: object) -> InputProcessorKind:
    kind = INPUT_PROCESSOR_KINDS.get(value) if isinstance(value, str) else None
    for _ in filter(None, [kind is None]):
        raise ValueError(REQUIRED.format(key=key, value=value))
    return kind
