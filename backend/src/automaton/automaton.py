"""YAML parsing for the DFA definition and in-memory data structures."""
from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field

import simpleeval

logger = logging.getLogger(__name__)

from typing_extensions import TypedDict, Literal, Any

class SourceDict(TypedDict):
    type: Literal["text", "base64"]
    media_type: str
    data: str

@dataclass
class MemoryArchive:
    filename: str
    source: SourceDict

@dataclass
class Action:
    name: str
    ui_label: str
    ui_button: str
    target: str
    ui_description: str | None = None
    trigger: str | None = None
    action_prompt: str | None = None
    attachments: dict[str, MemoryArchive] = field(default_factory=dict[str, MemoryArchive])

@dataclass
class State:
    key: str
    ui_label: str
    # Derived at load time as `len(actions) == 0`, not read from YAML —
    # structurally impossible to desync from the actual actions list.
    final: bool
    ui_description: str | None = None
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
    attachments: dict[str, MemoryArchive] = field(default_factory=dict[str, MemoryArchive])
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
    definition: str
    # Attachments for this signal's definition, sent only with the signals
    # computation call (never with normal chat turns).
    attachments: dict[str, MemoryArchive] = field(default_factory=dict[str, MemoryArchive])
    ui_description: str | None = None


class ActionPayload(TypedDict):
    name: str
    ui_label: str
    ui_button: str
    target: str
    has_trigger: bool

class StatePayload(TypedDict):
    key: str
    ui_label: str
    ui_description: str | None
    final: bool
    on_enter: str | None
    chat: bool
    actions: list[ActionPayload]

class SignalPayload(TypedDict):
    name: str
    ui_label: str | None
    ui_description: str | None
    definition: str
    attachments: dict[str, MemoryArchive]
    error: bool | None

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
        attachments: dict[str, MemoryArchive],
        general_attachments: dict[str, MemoryArchive],
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
        self.general_attachments = general_attachments
        self.attachments = attachments
        self.autotracking_on_user_message = autotracking_on_user_message
        self.autotracking_on_ai_message = autotracking_on_ai_message

    def get_state(self, state_key: str) -> State:
        return self.states[state_key]

    @staticmethod
    def get_state_payload(state: State) -> StatePayload:
        """Serializes `state` into the plain-dict shape every
        state-reporting endpoint sends to the frontend — the one place
        this shape is built, so it can't drift between call sites.
        Safety barrier: the reserved implicit state ("") must never reach
        a caller outside ChatService.open_if_needed — see its docstring."""
        if state.key == "":
            raise RuntimeError("Refusing to serialize the implicit initial state ('').")
        return {
            "key": state.key,
            "ui_label": state.ui_label,
            "ui_description": state.ui_description,
            "final": state.final,
            "on_enter": state.on_enter,
            "chat": state.chat,
            "actions": [
                {
                    "name": a.name,
                    "ui_label": a.ui_label,
                    "ui_button": a.ui_button,
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

    def triggers_reference(self, state_key: str, names: set[str]) -> bool:
        """Whether any triggerable action leaving `state_key` references at
        least one of `names` in its trigger expression. Generic (`names` is
        just a set the caller decides the meaning of) — lets a caller skip
        resolving an expensive extra value set (e.g. metrics_framework's
        metrics, see chat/metrics_service.py's merge_if_referenced) before
        evaluation, whenever nothing in this state's triggers could
        possibly use it."""
        state = self.states[state_key]
        return any(
            action.trigger and trigger_signal_names(action.trigger) & names for action in state.actions
        )

    def evaluate_triggers(self, state_key: str, signals: dict[str, Any]) -> str | None:
        """Returns the first action (YAML order) whose trigger evaluates
        true — FIFO priority — or None. Actions without `trigger` stay
        manual-only, never returned here."""
        state = self.states[state_key]
        for action in state.actions:
            if action.trigger and self._eval_trigger(action.trigger, signals):
                return action.name
        return None

    def preview_triggers(self, state_key: str, signals: dict[str, Any]) -> list:
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
    def _eval_trigger(expression: str, signals: dict[str, Any]) -> bool:
        """A malformed expression must never crash the caller: treat
        evaluation failures as False, with a warning. A referenced signal
        that hasn't been computed yet (value None — routine early in a
        conversation, or right after a failed computation) is a distinct,
        expected case: evaluating e.g. `signal > 5` against None would
        always raise, so short-circuit to False without even trying,
        instead of logging a warning for something that isn't wrong.
        (Every referenced name is guaranteed to be a real signal by now —
        see automaton_builder.py's _actions_sanity_check at build time —
        so a None here only ever means "not computed yet", never a typo.)
        """
        try:
            if any(signals.get(name) is None for name in trigger_signal_names(expression)):
                return False
            return bool(simpleeval.simple_eval(expression, names=signals))
        except Exception as exc:
            logger.warning("Trigger evaluation failed for expression '%s': %s", expression, exc)
            return False


