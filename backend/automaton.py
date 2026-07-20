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


class Automaton:
    def __init__(self, initial_state: str, states: dict[str, State]):
        self.initial_state = initial_state
        self.states = states

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
        states[key] = State(
            key=key,
            label=raw_state["label"],
            final=raw_state["final"],
            description=raw_state["description"].strip(),
            contextual_prompt=raw_state["contextual_prompt"],
            actions=actions,
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

    return Automaton(initial_state=initial_state, states=states)
