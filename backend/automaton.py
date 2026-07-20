"""YAML parsing for the DFA definition and in-memory data structures."""
from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path

import simpleeval
import yaml

logger = logging.getLogger(__name__)

# transition_log_level must be one of these (see State.transition_log_level).
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@dataclass
class Action:
    name: str
    label: str
    button_text: str
    target: str
    # Boolean expression (simpleeval syntax) over signal names, evaluated by
    # evaluate_triggers()/preview_triggers() for auto-tracking. None means the
    # action is only ever triggered manually.
    trigger: str | None = None


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
    # Log level (name) used when logging a transition landing on this state.
    transition_log_level: str = "WARNING"


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


def trigger_signal_names(expression: str) -> set[str]:
    """Free variable names referenced by a trigger expression, e.g.
    "daysSinceLastEvent >= 85 and stabilityConfidence >= 70" ->
    {"daysSinceLastEvent", "stabilityConfidence"}. Used both to validate
    triggers reference known signals at boot, and to report which signal
    values drove an auto-triggered transition."""
    tree = ast.parse(expression, mode="eval")
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _eval_trigger(expression: str, signals: dict) -> bool:
    """A malformed expression or a signal with value None must never crash
    the caller: treat evaluation failures as simply False, with a warning."""
    try:
        return bool(simpleeval.simple_eval(expression, names=signals))
    except Exception as exc:
        logger.warning("Trigger evaluation failed for expression '%s': %s", expression, exc)
        return False


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

    def evaluate_triggers(self, current_state: str, signals: dict) -> str | None:
        """Returns the name of the action to apply by FIFO priority (the
        first action, in YAML definition order, whose trigger evaluates
        true), or None if no condition is satisfied. Actions without a
        `trigger` field are ignored — they remain available as manual-only
        buttons."""
        state = self.get_state(current_state)
        for action in state.actions:
            if action.trigger and _eval_trigger(action.trigger, signals):
                return action.name
        return None

    def preview_triggers(self, current_state: str, signals: dict) -> list[dict]:
        """Every triggerable action in the current state with its expression
        and evaluation result, in FIFO priority order — for UI display only,
        never applies a transition."""
        state = self.get_state(current_state)
        results = []
        winner_found = False
        for action in state.actions:
            if not action.trigger:
                continue
            result = _eval_trigger(action.trigger, signals)
            would_fire = result and not winner_found
            winner_found = winner_found or result
            results.append({
                "action_name": action.name,
                "target": action.target,
                "trigger": action.trigger,
                "result": result,
                "would_fire": would_fire,
            })
        return results


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
                trigger=raw_action.get("trigger"),
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
            transition_log_level=raw_state.get("transition_log_level", "WARNING"),
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
        if state.transition_log_level not in VALID_LOG_LEVELS:
            raise ValueError(
                f"State '{state.key}': transition_log_level "
                f"'{state.transition_log_level}' must be one of {sorted(VALID_LOG_LEVELS)}"
            )
        for action in state.actions:
            if action.target not in states:
                raise ValueError(
                    f"State '{state.key}', action '{action.name}': "
                    f"target '{action.target}' is not a valid state"
                )
            if action.trigger:
                try:
                    referenced_names = trigger_signal_names(action.trigger)
                except SyntaxError as exc:
                    raise ValueError(
                        f"State '{state.key}', action '{action.name}': "
                        f"trigger '{action.trigger}' is not a valid expression: {exc}"
                    ) from exc
                unknown_names = referenced_names - seen_signal_names
                if unknown_names:
                    raise ValueError(
                        f"State '{state.key}', action '{action.name}': "
                        f"trigger references undefined signal(s): {', '.join(sorted(unknown_names))}"
                    )

    return Automaton(
        initial_state=initial_state,
        states=states,
        general_instructions=general_instructions,
        signals=signals,
    )
