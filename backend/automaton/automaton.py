"""YAML parsing for the DFA definition and in-memory data structures."""
from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field

import simpleeval

logger = logging.getLogger(__name__)

@dataclass
class Attachment:
    filename: str  # path relative to the model's own directory, also used as the display title
    # Anthropic `document` source shape, precomputed at load time:
    # {"type": "text"|"base64", "media_type": ..., "data": ...}.
    # Provider-neutral: consumers only look at type/data.
    source: dict


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
    # Derived at load time as `len(actions) == 0`, not read from YAML —
    # structurally impossible to desync from the actual actions list.
    final: bool
    description: str
    contextual_prompt: str
    actions: list[Action] = field(default_factory=list)
    # If set, the state doesn't generate free-form replies: the caller must
    # return this message (translated into the user's language) as-is.
    fixed_message: str | None = None
    # Log level (name) used when logging a transition landing on this state.
    transition_log_level: str = "WARNING"
    attachments: list[Attachment] = field(default_factory=list)
    on_enter: str | None = None


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
    # Attachments for this signal's ai_prompt, sent only with the signals
    # computation call (never with normal chat turns).
    attachments: list[Attachment] = field(default_factory=list)


def trigger_signal_names(expression: str) -> set[str]:
    """Free variable names in a trigger expression, e.g.
    "daysSinceLastEvent >= 85" -> {"daysSinceLastEvent"}. Used to validate
    triggers at boot and to report which signals drove a transition."""
    tree = ast.parse(expression, mode="eval")
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


class Automaton(object):
    """Stateless DFA definition: states, actions, prompts, signals — loaded
    once from YAML and never mutated afterward. It holds no notion of
    "current state"; every method that needs one takes it as an explicit
    `state_key` argument. The actual current state lives in the database
    (see db.get_current_state/save_transition) — callers read it from
    there and thread it through explicitly."""

    def __init__(
        self,
        initial_state: str,
        states: dict[str, State],
        general_prompt: str,
        signals: list[Signal],
        general_prompt_attachments: list[Attachment],
    ):
        self.initial_state = initial_state
        self.states = states
        self.general_prompt = general_prompt
        self.signals = signals
        self.general_prompt_attachments = general_prompt_attachments

    def get_state(self, state_key: str) -> State:
        return self.states[state_key]

    def get_state_payload(self, state_key: str) -> dict:
        """Serializes `state_key`'s State into the plain-dict shape every
        state-reporting endpoint sends to the frontend — the one place
        this shape is built, so it can't drift between call sites."""
        state = self.states[state_key]
        return {
            "key": state.key,
            "label": state.label,
            "description": state.description,
            "final": state.final,
            "on_enter": state.on_enter,
            "actions": [
                {
                    "name": a.name,
                    "label": a.label,
                    "button_text": a.button_text,
                    "target": a.target,
                }
                for a in state.actions
            ],
        }

    def move(self, state_key: str, action_name: str) -> Action:
        state = self.states[state_key]
        for action in state.actions:
            if action.name == action_name:
                return action
        raise ValueError(
            f"Action '{action_name}' not available in state '{state.key}'"
        )

    def evaluate_triggers(self, state_key: str, signals: dict) -> str | None:
        """Returns the first action (YAML order) whose trigger evaluates
        true — FIFO priority — or None. Actions without `trigger` stay
        manual-only, never returned here."""
        state = self.states[state_key]
        for action in state.actions:
            if action.trigger and self._eval_trigger(action.trigger, signals):
                return action.name
        return None

    def preview_triggers(self, state_key: str, signals: dict) -> list[dict]:
        """Every triggerable action in `state_key` with its expression and
        evaluation result, in FIFO priority order — for UI display only,
        never applies a transition."""
        state = self.states[state_key]
        results = []
        winner_found = False
        for action in state.actions:
            if not action.trigger:
                continue
            result = self._eval_trigger(action.trigger, signals)
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

    @staticmethod
    def _eval_trigger(expression: str, signals: dict) -> bool:
        """A malformed expression or a signal with value None must never
        crash the caller: treat evaluation failures as False, with a
        warning."""
        try:
            return bool(simpleeval.simple_eval(expression, names=signals))
        except Exception as exc:
            logger.warning("Trigger evaluation failed for expression '%s': %s", expression, exc)
            return False


