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
    # Extra generation instruction for this specific transition, on top of
    # the destination state's own context — see ChatService._generate_
    # action_prompt_message. None means no extra message for this action.
    action_prompt: str | None = None


@dataclass
class State:
    key: str
    label: str
    # Derived at load time as `len(actions) == 0`, not read from YAML —
    # structurally impossible to desync from the actual actions list.
    final: bool
    description: str
    # Required unless fixed_message is set — the two are mutually exclusive
    # (see AutomatonBuilder.build): a fixed_message state never generates
    # free-form content, so it has no use for one.
    contextual_prompt: str | None = None
    actions: list[Action] = field(default_factory=list)
    # If set, the state doesn't generate free-form replies: the caller must
    # return this message (translated into the user's language) as-is.
    fixed_message: str | None = None
    # Log level (name) used when logging a transition landing on this state.
    transition_log_level: str = "WARNING"
    attachments: list[Attachment] = field(default_factory=list)
    on_enter: str | None = None
    # If true, messages from before the transition into this state are kept
    # out of both the AI reply and auto-tracking's signal evaluation.
    history_cutoff: bool = False
    # If false, chat turns are rejected while this is the current state
    # (see ChatService._process_turn_locked) — independent of
    # fixed_message/history_cutoff: neither implies this.
    chat: bool = True

    @property
    def has_triggerable_actions(self) -> bool:
        """Whether any action leaving this state has a trigger — the one
        place that's decided, reused both to skip auto-tracking outright
        when there's nothing it could evaluate (see
        ChatService._run_auto_tracking) and, per action, for the
        "has_trigger" field get_state_payload sends the frontend (same
        `a.trigger is not None` check, just per-action instead of any())."""
        return any(a.trigger is not None for a in self.actions)


@dataclass
class Signal:
    name: str
    ui_label: str
    description: str
    definition: str
    # Attachments for this signal's definition, sent only with the signals
    # computation call (never with normal chat turns).
    attachments: list[Attachment] = field(default_factory=list)


def trigger_signal_names(expression: str) -> set[str]:
    """Free variable names in a trigger expression, e.g.
    "daysSinceLastEvent >= 85" -> {"daysSinceLastEvent"}. Used to validate
    triggers at boot and to report which signals drove a transition."""
    tree = ast.parse(expression, mode="eval")
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


class Automaton(object):

    def __init__(
        self,
        init_action: Action,
        states: dict[str, State],
        general_prompt: str,
        signals: list[Signal],
        general_prompt_attachments: list[Attachment],
        autotracking_on_user_message: bool,
        autotracking_on_ai_message: bool,
    ):
        # Replaces the old bare `initial_state: str` field: same
        # information (its .target), but as a real action so it can carry
        # an action_prompt too — see ChatService.open_if_needed, the one
        # place that ever executes it.
        self.init_action = init_action
        self.states = states
        self.general_prompt = general_prompt
        self.signals = signals
        self.general_prompt_attachments = general_prompt_attachments
        self.autotracking_on_user_message = autotracking_on_user_message
        self.autotracking_on_ai_message = autotracking_on_ai_message

    def get_state(self, state_key: str) -> State:
        return self.states[state_key]

    @staticmethod
    def get_state_payload(state: State) -> dict:
        """Serializes `state` into the plain-dict shape every
        state-reporting endpoint sends to the frontend — the one place
        this shape is built, so it can't drift between call sites.
        Safety barrier: the reserved implicit state ("") must never reach
        a caller outside ChatService.open_if_needed — see its docstring."""
        if state.key == "":
            raise RuntimeError("Refusing to serialize the implicit initial state ('').")
        return {
            "key": state.key,
            "label": state.label,
            "description": state.description,
            "final": state.final,
            "on_enter": state.on_enter,
            "chat": state.chat,
            "actions": [
                {
                    "name": a.name,
                    "label": a.label,
                    "button_text": a.button_text,
                    "target": a.target,
                    # Not the trigger expression itself, just whether one is
                    # set — the frontend uses this to decide button
                    # visibility (see ActionButtons.vue), never to evaluate
                    # anything itself.
                    "has_trigger": a.trigger is not None,
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


