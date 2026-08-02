from automaton.automaton import Action, MemoryArchive, Automaton, Signal, SourceDict, State, trigger_signal_names
from typing import Any
from metrics.metrics_framework import metric_names

import yaml
import base64
from pathlib import Path

EXTENSION_TO_MEDIA_TYPE = {
    ".yml": "text/plain",
    ".md": "text/plain",
    ".txt": "text/plain",
    ".csv": "text/plain",
}

VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# The fixed set of always-computed keys chat.env.Env provides on every
# read (never persisted — see its own module docstring), reserved here
# too so a trigger can reference one (e.g. "state_duration_in_minutes >= 30")
# without failing the same undefined-name validation signal/metric names
# already go through below — chat.env.Env imports this constant back,
# rather than the reverse, to keep automaton/ free of any dependency on
# chat/ (chat/ already depends on automaton/, never the other way around).
ENV_COMPUTED_KEYS = (
    "today",
    "time",
    "current_session_duration_in_minutes",
    "last_user_session_datetime",
    "number_of_user_sessions",
    "state_duration_in_minutes",
)

class AutomatonBuilder(object):
    """Builds an Automaton from a project's index.yml: parses the YAML,
    resolves attachments, validates the result, and constructs the
    Automaton — the one place that shape is decided."""

    @staticmethod
    def _convert_contents_to_archives(contents: dict) -> dict[str, MemoryArchive]:
        archives = {}
        for archive_name, archive_contents in contents.items():
            extension = Path(archive_name).suffix.lower()
            media_type = EXTENSION_TO_MEDIA_TYPE.get(extension, "application/octet-stream")
            if media_type == "text/plain":
                source = {
                    "type": "text",
                    "media_type": "text/plain",
                    "data": archive_contents,
                }
            else:
                if isinstance(archive_contents, str):
                    data_bytes = archive_contents.encode("utf-8")
                elif isinstance(archive_contents, (bytes, bytearray)):
                    data_bytes = archive_contents
                else:
                    raise TypeError(f"Contenuto di '{archive_name}' non valido: atteso str o bytes, ricevuto {type(archive_contents).__name__}")
                encoded = base64.b64encode(data_bytes).decode("ascii")
                source : SourceDict = {"type": "base64", "media_type": media_type, "data": encoded}
            archives[archive_name] = MemoryArchive(filename=archive_name, source=source)
        return archives

    @staticmethod
    def _extract_required_archives(required_attachments: list[str], all_archives: dict[str, MemoryArchive], for_field: str) -> dict[str, MemoryArchive]:
        extracted_archives = {}
        for required_attachment in required_attachments:
            if required_attachment not in all_archives:
                raise ValueError(
                    f"{for_field} attachment named '{required_attachment}' not found"
                )
            extracted_archives[required_attachment] = all_archives[required_attachment]
        return extracted_archives

    def _build_signal(self, name, raw_signal: dict, all_archives: dict[str, MemoryArchive]) -> Signal:
        return Signal(
            name=name,
            ui_label=raw_signal.get("ui-label", name),
            ui_description=raw_signal["ui-description"].strip() if raw_signal.get("ui-description") else raw_signal["definition"].strip(),
            definition=raw_signal["definition"].strip(),
            attachments=self._extract_required_archives(
                raw_signal.get("attachments", []), all_archives, f"signal '{name}'"
            )
        )


    @staticmethod
    def _build_action_env(raw_env: Any, action_name: str) -> dict[str, str] | None:
        """{key: expression} straight from YAML, normalized to always be
        Python-expression *source* (see Action.env's own docstring) —
        `True`/`42`/`null`/... parse as native YAML types rather than
        strings, but still need to round-trip through the same
        simpleeval-based evaluator a `trigger` does (see Automaton.
        eval_action_env), so anything that isn't already a str is
        stringified into its own Python-literal spelling here instead of
        at every evaluation."""
        if not raw_env:
            return None
        if not isinstance(raw_env, dict):
            raise ValueError(
                f"Action '{action_name}': 'env' must be a mapping of key -> expression, "
                f"got {type(raw_env).__name__}."
            )
        return {key: value if isinstance(value, str) else str(value) for key, value in raw_env.items()}

    def _build_action(self, key: str, raw_action: dict, all_archives: dict[str, MemoryArchive]) -> Action:
        return Action(
            name=raw_action["name"],
            ui_description=raw_action.get("ui-description"),
            ui_label=raw_action.get("ui-label") or raw_action["name"],
            ui_button=raw_action.get("ui-button") or raw_action.get("ui-label") or raw_action["name"],
            # Missing 'target' means a self-loop: the action stays
            # on the state it fired from.
            target=raw_action.get("target", key),
            trigger=raw_action.get("trigger"),
            action_prompt=raw_action["action-prompt"].strip() if raw_action.get("action-prompt") else None,
            attachments=self._extract_required_archives(raw_action.get("attachments", []), all_archives, f"action {raw_action['name']}"),
            on_enter=raw_action.get("on-enter"),
            env=self._build_action_env(raw_action.get("env"), raw_action["name"]),
        )

    def _build_state(self, key: str, raw_state: dict, all_archives: dict[str, MemoryArchive]) -> State:
        raw_actions = raw_state.get("actions", [])
        actions = [self._build_action(key, raw_action, all_archives)
                   for raw_action in raw_actions]
        fixed_message = raw_state.get("fixed-message")
        contextual_prompt = raw_state.get("contextual-prompt")

        if fixed_message and contextual_prompt is not None:
            raise ValueError(
                f"State '{key}': 'fixed-message' and 'contextual-prompt' are mutually "
                "exclusive — a fixed-message state never generates free-form content, "
                "so it has no use for a contextual-prompt."
            )
        if not fixed_message and contextual_prompt is None:
            raise ValueError(f"State '{key}': 'contextual-prompt' is required unless 'fixed-message' is set.")

        transition_log_level = raw_state.get("transition-log-level", "WARNING")
        if transition_log_level not in VALID_LOG_LEVELS:
            raise ValueError(
                f"State '{key}': transition-log-level "
                f"'{transition_log_level}' must be one of {sorted(VALID_LOG_LEVELS)}"
            )

        return State(
            key=key,
            ui_label=raw_state.get("ui-label", key),
            final=len(actions) == 0,
            ui_description=raw_state["ui-description"].strip() if raw_state.get("ui-description") else None,
            contextual_prompt=contextual_prompt.strip() if contextual_prompt else None,
            actions=actions,
            fixed_message=fixed_message.strip() if fixed_message else None,
            transition_log_level=transition_log_level,
            attachments=self._extract_required_archives(raw_state.get("attachments", []), all_archives, f"state '{key}'"),
            history_cutoff=raw_state.get("history-cutoff", False),
            chat=raw_state.get("chat", True),
        )

    def _actions_sanity_check(self, key: str, state: State, declared_states: set[str], valid_trigger_names: set[str]):
        """`valid_trigger_names` is every name a trigger expression may
        reference: this project's own declared signals, every core metric
        name (see metrics_framework.metric_names), and every always-
        computed env key (see ENV_COMPUTED_KEYS) — a trigger can compare
        against any of the three interchangeably, see automaton.py's
        Automaton.triggers_reference/evaluate_triggers. A project's own
        free-form [env] values (see chat.env.Env/chat.metadata_handler.
        MetadataHandler) are deliberately not included here: their names
        are only ever known at runtime (whatever the model has reported
        so far), never at build time, so they can't be validated the same
        way — referencing one in a trigger fails this check exactly like
        referencing any other genuinely undefined name would. An action's
        own `env` expressions (see Action.env/Automaton.eval_action_env)
        get only a syntax check here, deliberately not the same
        unknown-name check: unlike a trigger, an env expression's whole
        point is often to reference (and update) a project's own
        free-form env key — e.g. `number_of_steps: number_of_steps + 1`
        — which is exactly one of these runtime-only names this method
        can't see at build time either."""
        for action in state.actions:
                if action.target not in declared_states:
                    raise ValueError(
                        f"State '{state.key}', action '{action.name}': "
                        f"target '{action.target}' is not a valid state"
                    )
                if action.trigger:
                    try:
                     referenced_names = trigger_signal_names(action.trigger)
                    except SyntaxError as exc:
                        raise ValueError(
                            f"State {key}, action '{action.name}': "
                            f"trigger '{action.trigger}' is not a valid expression: {exc}"
                        ) from exc
                    unknown_names = referenced_names - valid_trigger_names
                    if unknown_names:
                        raise ValueError(
                            f"Action '{action.name}': "
                            f"trigger references undefined signal(s)/metric(s)/env value(s): {', '.join(sorted(unknown_names))}"
                        )
                if action.env:
                    for env_key, expression in action.env.items():
                        try:
                            trigger_signal_names(expression)
                        except SyntaxError as exc:
                            raise ValueError(
                                f"State {key}, action '{action.name}': "
                                f"env expression for '{env_key}' ('{expression}') is not a valid expression: {exc}"
                            ) from exc

    def _build_init_action(self, raw: dict) -> Action:
        raw_init_action = raw.get("init-action")
        if not isinstance(raw_init_action, dict) or not raw_init_action.get("target"):
            raise ValueError(
                "'init-action' is required and must be a mapping with at least a 'target' "
                "field — the project's real starting state."
            )
        init_action = Action(
            name="init-action",
            ui_label=raw_init_action.get("ui-label", "init-action"),
            ui_button="",
            trigger="True",
            target=raw_init_action["target"],
            action_prompt=raw_init_action["action-prompt"].strip() if raw_init_action.get("action-prompt") else None,
            on_enter=raw_init_action.get("on-enter"),
        )
        return init_action


    def build(self, contents: dict) -> Automaton:
        all_archives = self._convert_contents_to_archives(contents=contents)

        raw = yaml.safe_load(contents['index.yml'])

        raw_signals = raw.get("signals", {})
        if not isinstance(raw_signals, dict):
            raise ValueError(f"'signals' must be a mapping of signal name -> fields, got {type(raw_signals).__name__}.")

        signals = {
            name: self._build_signal(name, raw_signal, all_archives)
            for name, raw_signal in raw_signals.items()
        }

        reserved_names = set(signals.keys()) & metric_names()
        if reserved_names:
            raise ValueError(
                "Signal name(s) reserved for core metrics (see metrics_framework) cannot be "
                f"reused as signal names: {', '.join(sorted(reserved_names))}"
            )
        # A trigger may reference a declared signal, a core metric, or an
        # always-computed env key interchangeably — see automaton.py's
        # Automaton.evaluate_triggers, whose caller merges metric/env
        # values into the same flat `names` dict (see chat/metrics_
        # service.py's merge_if_referenced, chat/env.py's own equivalent).
        valid_trigger_names = set(signals.keys()) | metric_names() | set(ENV_COMPUTED_KEYS)

        raw_states = raw["states"]
        if not isinstance(raw_states, dict):
            raise ValueError(f"'states' must be a mapping of state name -> fields, got {type(raw_states).__name__}.")
        if "" in raw_states:
            raise ValueError(
                "State '' is reserved for the implicit initial state (see init-action) "
                "and cannot be declared in 'states'."
            )

        init_action = self._build_init_action(raw)
        states: dict[str, State] = {}
        states[""] = State(key="", ui_label="", final=False, ui_description="", actions=[init_action])
        self._actions_sanity_check(init_action.name, states[""], set(raw_states.keys()), valid_trigger_names)

        for key, raw_state in raw_states.items():
            if not isinstance(raw_state, dict):
                raise ValueError(
                    f"State '{key}': expected a mapping of fields (ui-label, ui-description, "
                    f"actions, ...), got {type(raw_state).__name__} instead. This usually "
                    "means a field meant to belong to a state (e.g. 'actions') was "
                    "indented as a sibling of the state's key rather than nested under it, "
                    "so YAML parsed it as its own separate state."
                )

            states[key] = self._build_state(key, raw_state, all_archives)
            self._actions_sanity_check(key, states[key], set(raw_states.keys()), valid_trigger_names)

        general_attachments = self._extract_required_archives(raw.get('attachments', []), all_archives, for_field="global")
        autotracking_on_user_message = raw.get("signal-tracking-on-user-message", True)
        autotracking_on_ai_message = raw.get("signal-tracking-on-ai-message", False)

        return Automaton(
            init_action=init_action,
            states=states,
            general_prompt=raw.get("general-prompt", ""),
            signals=list(signals.values()),
            general_attachments=general_attachments,
            attachments=all_archives,
            autotracking_on_user_message=autotracking_on_user_message,
            autotracking_on_ai_message=autotracking_on_ai_message,
        )
