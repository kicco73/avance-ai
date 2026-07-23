"""YAML parsing for the DFA definition and in-memory data structures."""
from __future__ import annotations

import ast
import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path

import simpleeval
import yaml

logger = logging.getLogger(__name__)

# transition_log_level must be one of these (see State.transition_log_level).
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# .md/.txt/.csv are sent as Anthropic `document` text sources; .pdf as base64.
# Not mimetypes.guess_type(): it returns "text/markdown" for .md, which the
# Anthropic document block API rejects. Anything else is rejected at boot.
EXTENSION_TO_MEDIA_TYPE = {
    ".md": "text/plain",
    ".txt": "text/plain",
    ".csv": "text/plain",
    ".pdf": "application/pdf",
}


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
    def __init__(
        self,
        initial_state: str,
        states: dict[str, State],
        general_prompt: str,
        signals: list[Signal],
        general_prompt_attachments: list[Attachment],
    ):
        self.initial_state = initial_state
        self.state = states[initial_state]
        self.states = states
        self.general_prompt = general_prompt
        self.signals = signals
        self.general_prompt_attachments = general_prompt_attachments

    def get_current_state(self) -> State:
        return self.state

    def get_current_state_payload(self) -> dict:
        """Serializes the current State into the plain-dict shape every
        state-reporting endpoint sends to the frontend — the one place
        this shape is built, so it can't drift between call sites."""
        state = self.state
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

    def log_transition(
        self,
        from_state: str,
        to_state: str,
        action_name: str,
        trigger_type: str,
        signal_values: dict | None = None,
    ) -> None:
        """Logs every state transition (manual or auto) at the destination
        state's `transition_log_level` (default WARNING) — the one place
        this is done, called by every part of the backend that transitions."""
        level = getattr(logging, self.states[to_state].transition_log_level)
        message = f"State transition: {from_state} -> {to_state} (action={action_name}, trigger={trigger_type})"
        if signal_values:
            message += f" signals={signal_values}"
        logger.log(level, message)

    def apply_action(self, action_name: str) -> Action:
        state = self.state
        for action in state.actions:
            if action.name == action_name:
                self.state = self.states[action.target]
                return action
        raise ValueError(
            f"Action '{action_name}' not available in state '{state.key}'"
        )

    def set_current_state(self, key: str) -> None:
        """Hydrates current state from an external source of truth:
        db.get_current_state() at boot, or initial_state on reset. The
        only way to move state besides apply_action()."""
        self.state = self.states[key]

    def evaluate_triggers(self, signals: dict) -> str | None:
        """Returns the first action (YAML order) whose trigger evaluates
        true — FIFO priority — or None. Actions without `trigger` stay
        manual-only, never returned here."""
        state = self.state
        for action in state.actions:
            if action.trigger and self._eval_trigger(action.trigger, signals):
                return action.name
        return None

    def preview_triggers(self, signals: dict) -> list[dict]:
        """Every triggerable action in the current state with its expression
        and evaluation result, in FIFO priority order — for UI display only,
        never applies a transition."""
        state = self.state
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


class AutomatonBuilder(object):
    """Builds an Automaton from a model's index.yml: parses the YAML,
    resolves attachments, validates the result, and constructs the
    Automaton — the one place that shape is decided."""

    @staticmethod
    def _load_attachments(paths: list[str], field_description: str, base_dir: Path) -> list[Attachment]:
        """Reads attachment files once per build() call, resolved relative
        to `base_dir` (the YAML file's own directory). Raises ValueError
        for an unsupported extension or a missing file."""
        attachments = []
        for rel_path in paths:
            extension = Path(rel_path).suffix.lower()
            if extension not in EXTENSION_TO_MEDIA_TYPE:
                raise ValueError(
                    f"{field_description}: attachment '{rel_path}' has unsupported extension "
                    f"'{extension}'. Supported: {sorted(EXTENSION_TO_MEDIA_TYPE)}"
                )
            full_path = base_dir / rel_path
            if not full_path.is_file():
                raise ValueError(
                    f"{field_description}: attachment '{rel_path}' not found in {base_dir}"
                )
            media_type = EXTENSION_TO_MEDIA_TYPE[extension]
            if media_type == "text/plain":
                source = {
                    "type": "text",
                    "media_type": "text/plain",
                    "data": full_path.read_text(encoding="utf-8"),
                }
            else:
                encoded = base64.b64encode(full_path.read_bytes()).decode("ascii")
                source = {"type": "base64", "media_type": media_type, "data": encoded}
            attachments.append(Attachment(filename=rel_path, source=source))
        return attachments

    def build(self, path: str | Path) -> Automaton:
        path = Path(path)
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        # Attachments are resolved relative to wherever this specific YAML file
        # lives, not a shared fixed directory — each model carries its own.
        base_dir = path.parent

        initial_state = raw["initial_state"]
        general_prompt = raw["general_prompt"].strip()
        general_prompt_attachments = self._load_attachments(
            raw.get("attachments", []), "general_prompt", base_dir
        )
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
                # Derived, not read from YAML: a state is final iff it has no
                # outgoing actions. Keeps the flag structurally impossible to
                # desync from the actual `actions` list.
                final=len(actions) == 0,
                description=raw_state["description"].strip(),
                on_enter=raw_state["on_enter"] if "on_enter" in raw_state else None,
                contextual_prompt=raw_state["contextual_prompt"],
                actions=actions,
                fixed_message=fixed_message.strip() if fixed_message else None,
                transition_log_level=raw_state.get("transition_log_level", "WARNING"),
                attachments=self._load_attachments(raw_state.get("attachments", []), f"state '{key}'", base_dir),
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
                    attachments=self._load_attachments(
                        raw_signal.get("attachments", []), f"signal '{name}'", base_dir
                    ),
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
            general_prompt=general_prompt,
            signals=signals,
            general_prompt_attachments=general_prompt_attachments,
        )
