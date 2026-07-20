"""YAML parsing for the DFA definition and in-memory data structures."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Action:
    name: str
    label: str
    button_text: str
    target: str


@dataclass
class State:
    key: str
    label: str
    final: bool
    description: str
    contextual_prompt: str
    actions: list[Action] = field(default_factory=list)
    # If set, the state doesn't generate free-form replies: the caller must
    # return this message (translated into the user's language) as-is.
    fixed_message: str | None = None


@dataclass
class Signal:
    name: str
    ui_label: str
    description: str
    ai_prompt: str
    # Documentation-only for now: marks signals meant to eventually be computed
    # deterministically by the backend instead of estimated by the AI. Has no
    # effect on behavior yet — every signal is evaluated the same way.
    placeholder_builtin: bool = False


class Automaton:
    def __init__(
        self,
        initial_state: str,
        states: dict[str, State],
        general_instructions: str,
        signals: list[Signal],
    ):
        self.initial_state = initial_state
        self.states = states
        self.general_instructions = general_instructions
        self.signals = signals

    def get_state(self, key: str) -> State:
        return self.states[key]

    def apply_action(self, current_state_key: str, action_name: str) -> str:
        """Returns the resulting state key, or raises ValueError if the action is not valid."""
        state = self.get_state(current_state_key)
        for action in state.actions:
            if action.name == action_name:
                return action.target
        raise ValueError(
            f"Action '{action_name}' not available in state '{current_state_key}'"
        )


def load_automaton(path: str | Path) -> Automaton:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    initial_state = raw["initial_state"]
    general_instructions = raw["general_instructions"].strip()
    raw_states = raw["states"]

    states: dict[str, State] = {}
    for key, raw_state in raw_states.items():
        actions = [
            Action(
                name=raw_action["name"],
                label=raw_action["label"],
                button_text=raw_action["button_text"],
                target=raw_action["target"],
            )
            for raw_action in raw_state.get("actions", [])
        ]
        fixed_message = raw_state.get("fixed_message")
        states[key] = State(
            key=key,
            label=raw_state["label"],
            final=raw_state["final"],
            description=raw_state["description"].strip(),
            contextual_prompt=raw_state["contextual_prompt"],
            actions=actions,
            fixed_message=fixed_message.strip() if fixed_message else None,
        )

    signals: list[Signal] = []
    seen_signal_names: set[str] = set()
    for raw_signal in raw.get("signals", []):
        name = raw_signal["name"]
        if name in seen_signal_names:
            raise ValueError(f"Duplicate signal name '{name}' in 'signals'")
        seen_signal_names.add(name)
        signals.append(
            Signal(
                name=name,
                ui_label=raw_signal["ui_label"],
                description=raw_signal["description"].strip(),
                ai_prompt=raw_signal["ai_prompt"].strip(),
                placeholder_builtin=raw_signal.get("placeholder_builtin", False),
            )
        )

    if initial_state not in states:
        raise ValueError(f"initial_state '{initial_state}' is not defined among the states")

    for state in states.values():
        for action in state.actions:
            if action.target not in states:
                raise ValueError(
                    f"State '{state.key}', action '{action.name}': "
                    f"target '{action.target}' is not a valid state"
                )

    return Automaton(
        initial_state=initial_state,
        states=states,
        general_instructions=general_instructions,
        signals=signals,
    )
