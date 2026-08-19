from automaton.automaton import (
    Action, MemoryArchive, Automaton, Signal, SourceDict, State,
    trigger_bare_names, trigger_namespace_refs, trigger_type_violations,
)
from automaton.identifier_registry import build_registry
from typing import Any
from metrics.metrics_framework import metric_names

from ruamel.yaml import YAML
import base64
from pathlib import Path

_yaml = YAML(typ='rt')

EXTENSION_TO_MEDIA_TYPE = {
    ".yml": "text/plain",
    ".md": "text/plain",
    ".txt": "text/plain",
    ".csv": "text/plain",
}

VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

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
        actions: list[Action] = []
        action_names_by_ui_label: dict[str, str] = {}
        for raw_action in raw_actions:
            action = self._build_action(key, raw_action, all_archives)
            existing_name = action_names_by_ui_label.get(action.ui_label)
            if existing_name is not None:
                raise ValueError(
                    f"State '{key}': actions '{existing_name}' and '{action.name}' both use "
                    f"ui-label '{action.ui_label}' — ui-label must be unique within a state."
                )
            action_names_by_ui_label[action.ui_label] = action.name
            actions.append(action)
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

    @staticmethod
    def _validate_namespaced_expression(expression: str, context: str, registry: dict[str, dict[str, str]]) -> None:
        """Syntax + per-namespace identifier validation shared by both
        `trigger:` and an action's own `env:` expressions (see automaton.
        automaton's RESERVED_NAMESPACES/trigger_namespace_refs/
        trigger_bare_names) — `context` is just this expression's own
        description, for the error message. `registry` (see automaton.
        identifier_registry.build_registry) is the single source of every
        valid identifier per namespace — including `env.*`, whose valid
        names are only known project-wide at build time: every action's
        own declared `env:` key, collected across the whole project
        before this ever runs (see build()), so there's no longer a
        runtime-only name it can't see. Any bare (unnamespaced) identifier
        left over must be a core metric (see metrics_framework.
        metric_names) — anything else, namespaced or not, is undefined."""
        try:
            namespace_refs = trigger_namespace_refs(expression)
            bare_names = trigger_bare_names(expression)
        except SyntaxError as exc:
            raise ValueError(f"{context} ('{expression}') is not a valid expression: {exc}") from exc

        unknown = set()
        for namespace, refs in namespace_refs.items():
            valid = registry.get(namespace, {}).keys()
            unknown |= {f"{namespace}.{n}" for n in refs - valid}
        unknown |= bare_names - metric_names()
        if unknown:
            raise ValueError(f"{context} references undefined name(s): {', '.join(sorted(unknown))}")

    @staticmethod
    def _validate_trigger_types(expression: str, context: str) -> None:
        """Only for `trigger:` (see _actions_sanity_check's own call
        site) — never `env:`, which has no comparison shape to
        type-check in the first place (see Action.env's own docstring:
        "any simple value", not a boolean condition). See
        trigger_type_violations' own docstring for exactly what this
        catches (a comparison between two statically-known-incompatible
        types, e.g. `system.today() >= 5`) and why that's a build-time-
        checkable thing at all, unlike a signal's actual runtime value."""
        violations = trigger_type_violations(expression)
        if violations:
            raise ValueError(f"{context} ('{expression}'): {'; '.join(violations)}")

    def _actions_sanity_check(
        self, key: str, state: State, declared_states: set[str], registry: dict[str, dict[str, str]]
    ):
        """`registry` (see automaton.identifier_registry.build_registry):
        every valid identifier for this project, one set per namespace —
        `signal.*`/`env.*` project-specific, `system.*`/`session.*`/
        `session.metric.*`/`metric.*` the same fixed sets for every
        project (see that module's own docstring)."""
        for action in state.actions:
            if action.target not in declared_states:
                raise ValueError(
                    f"State '{state.key}', action '{action.name}': "
                    f"target '{action.target}' is not a valid state"
                )
            if action.trigger:
                self._validate_namespaced_expression(
                    action.trigger, f"State {key}, action '{action.name}': trigger", registry,
                )
                self._validate_trigger_types(
                    action.trigger, f"State {key}, action '{action.name}': trigger",
                )
            if action.env:
                for env_key, expression in action.env.items():
                    self._validate_namespaced_expression(
                        expression, f"State {key}, action '{action.name}': env expression for '{env_key}'", registry,
                    )

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
            target=raw_init_action["target"],
            action_prompt=raw_init_action["action-prompt"].strip() if raw_init_action.get("action-prompt") else None,
            on_enter=raw_init_action.get("on-enter"),
        )
        return init_action


    def build(self, contents: dict) -> Automaton:
        all_archives = self._convert_contents_to_archives(contents=contents)

        raw = _yaml.load(contents['index.yml'])

        raw_signals = raw.get("signals", {})
        if not isinstance(raw_signals, dict):
            raise ValueError(f"'signals' must be a mapping of signal name -> fields, got {type(raw_signals).__name__}.")

        signals: dict[str, Signal] = {}
        signal_names_by_ui_label: dict[str, str] = {}
        for name, raw_signal in raw_signals.items():
            signal = self._build_signal(name, raw_signal, all_archives)
            existing_name = signal_names_by_ui_label.get(signal.ui_label)
            if existing_name is not None:
                raise ValueError(
                    f"Signals '{existing_name}' and '{name}' both use ui-label "
                    f"'{signal.ui_label}' — ui-label must be unique across all signals."
                )
            signal_names_by_ui_label[signal.ui_label] = name
            signals[name] = signal

        reserved_names = set(signals.keys()) & metric_names()
        if reserved_names:
            raise ValueError(
                "Signal name(s) reserved for core metrics (see metrics_framework) cannot be "
                f"reused as signal names: {', '.join(sorted(reserved_names))}"
            )

        raw_states = raw["states"]
        if not isinstance(raw_states, dict):
            raise ValueError(f"'states' must be a mapping of state name -> fields, got {type(raw_states).__name__}.")
        if "" in raw_states:
            raise ValueError(
                "State '' is reserved for the implicit initial state (see init-action) "
                "and cannot be declared in 'states'."
            )

        # Pass 1: build every state/action, no expression validation yet
        # — every action's own declared `env:` keys (see valid_env_attrs
        # below) can only be known project-wide, once every one of them
        # has actually been built, so validation itself has to wait for
        # a second pass (see _actions_sanity_check's own calls below).
        init_action = self._build_init_action(raw)
        states: dict[str, State] = {}
        states[""] = State(key="", ui_label="", final=False, ui_description="", actions=[init_action])

        state_keys_by_ui_label: dict[str, str] = {}
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
            existing_key = state_keys_by_ui_label.get(states[key].ui_label)
            if existing_key is not None:
                raise ValueError(
                    f"States '{existing_key}' and '{key}' both use ui-label "
                    f"'{states[key].ui_label}' — ui-label must be unique across all states."
                )
            state_keys_by_ui_label[states[key].ui_label] = key

        # Pass 2: every action's own env: keys, across every state
        # (including the synthetic "" state's own init_action), plus
        # every declared signal — the project's own identifier registry
        # (see automaton.identifier_registry.build_registry), the single
        # source _actions_sanity_check validates every trigger/env:
        # expression's own namespace references against below.
        registry = build_registry(list(signals.values()), states)
        for key, state in states.items():
            context_key = init_action.name if key == "" else key
            self._actions_sanity_check(context_key, state, set(raw_states.keys()), registry)

        general_attachments = self._extract_required_archives(raw.get('attachments', []), all_archives, for_field="global")
        autotracking_on_ai_message = raw.get("signal-tracking-on-ai-message", False)

        return Automaton(
            init_action=init_action,
            states=states,
            general_prompt=raw.get("general-prompt", ""),
            signals=list(signals.values()),
            general_attachments=general_attachments,
            attachments=all_archives,
            autotracking_on_ai_message=autotracking_on_ai_message,
        )
