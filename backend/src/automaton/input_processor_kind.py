"""Which processor answers in a state, as far as a build can tell.

`ai` is the model: the state's prompt is read, its signals are computed,
and `chat.write` has no reader. `system` is the automaton alone: nothing
computes signals for its scripts, and what an action reaching it writes
with `chat.write` is the reply. The kind answers those questions at build
time; what runs a turn is turn/input_processor.py, keyed the same way."""
from __future__ import annotations

from typing import TYPE_CHECKING

from automaton.identifier_registry import IdentifierRegistry

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

    def script_registry(self, registry: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        raise NotImplementedError

    def on_exit_registry(self, registry: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
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

    def script_registry(self, registry: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        return registry

    def on_exit_registry(self, registry: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        chat = {
            method: text for method, text in registry.get("chat", {}).items()
            if method not in IdentifierRegistry.REPLY_METHODS
        }
        return {**registry, "chat": chat}

    def model_visible_io(self, state: "State") -> tuple[tuple[str, tuple[str, ...]], ...]:
        return (("input", tuple(state.input)), ("output", tuple(state.output)))


class SystemKind(InputProcessorKind):
    name = "system"

    def chat_enabled(self, declared: bool) -> bool:
        return False

    def check_prompt(self, key: str, contextual_prompt: str | None) -> None:
        return None

    def script_registry(self, registry: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        return IdentifierRegistry.excluding(registry, ("signal",))

    def on_exit_registry(self, registry: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        return registry

    def model_visible_io(self, state: "State") -> tuple[tuple[str, tuple[str, ...]], ...]:
        return ()


INPUT_PROCESSOR_KINDS: dict[str, InputProcessorKind] = {kind.name: kind for kind in (AiKind(), SystemKind())}


def kind_of(key: str, value: object) -> InputProcessorKind:
    kind = INPUT_PROCESSOR_KINDS.get(value) if isinstance(value, str) else None
    for _ in filter(None, [kind is None]):
        raise ValueError(REQUIRED.format(key=key, value=value))
    return kind
