from automaton.automaton import (
    Action, EnvKey, MemoryArchive, Automaton, Reaction, Signal, SourceDict, State,
    trigger_automaton_env_refs, trigger_automaton_project_refs, trigger_bare_names, trigger_namespace_refs,
    trigger_type_violations,
)
from automaton.identifier_registry import build_registry
from automaton.on_enter_script import OnEnterScriptError, OnEnterScriptSignatureParser
from typing import Any
from metrics.metrics_framework import metric_names

from ruamel.yaml import YAML
import base64
from pathlib import Path

_yaml = YAML(typ='rt')

# Stateless — one shared instance is enough (see OnEnterScriptSignature
# Parser's own docstring); every call site below is a plain
# AutomatonBuilder method, never itself an instance concern.
_on_enter_parser = OnEnterScriptSignatureParser()

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
    def _build_reaction(name: str, raw_reaction: dict) -> Reaction:
        return Reaction(
            name=name,
            ui_label=raw_reaction.get("ui-label", name),
            ui_description=raw_reaction["ui-description"].strip() if raw_reaction.get("ui-description") else raw_reaction["definition"].strip(),
            definition=raw_reaction["definition"].strip(),
        )

    @staticmethod
    def _build_env_key(name: str, raw_env_key: dict) -> EnvKey:
        """One `env:` declaration. `value` is normalized to expression
        *source*, exactly like an action's own `env:` field."""
        raw_value = (raw_env_key or {}).get("value", "")
        value = raw_value if isinstance(raw_value, str) else str(raw_value)
        raw_description = (raw_env_key or {}).get("ui-description")
        return EnvKey(
            name=name,
            value=value.strip(),
            ui_description=raw_description.strip() if raw_description else None,
        )


    @staticmethod
    def _build_action_env(raw_env: Any, action_name: str) -> dict[str, str] | None:
        """{key: expression} from YAML, normalized to always be
        Python-expression *source*: `True`/`42`/`null`/... parse as
        native YAML types, so anything not already a str is stringified here."""
        if not raw_env:
            return None
        if not isinstance(raw_env, dict):
            raise ValueError(
                f"Action '{action_name}': 'env' must be a mapping of key -> expression, "
                f"got {type(raw_env).__name__}."
            )
        return {key: value if isinstance(value, str) else str(value) for key, value in raw_env.items()}

    @staticmethod
    def _validate_on_enter(on_enter: str | None, location: str) -> None:
        """`location` (e.g. "state 'a', action 'go'") is prepended to
        whatever OnEnterScriptSignatureParser.validate raises, re-raised
        as the same OnEnterScriptError type rather than a bare ValueError."""
        try:
            _on_enter_parser.validate(on_enter)
        except OnEnterScriptError as exc:
            raise OnEnterScriptError(f"{location}: invalid on-enter script — {exc}") from exc

    def _build_action(self, key: str, raw_action: dict, all_archives: dict[str, MemoryArchive]) -> Action:
        on_enter = raw_action.get("on-enter")
        self._validate_on_enter(on_enter, f"state '{key}', action '{raw_action['name']}'")
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
            on_enter=on_enter,
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
            reactions_enabled=raw_state.get("reactions-enabled", False),
        )

    @staticmethod
    def _validate_namespaced_expression(expression: str, context: str, registry: dict[str, dict[str, str]]) -> None:
        """Syntax + per-namespace identifier validation shared by
        `trigger:` and an action's `env:` expressions. Any bare
        identifier left over must be a core metric."""
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
        """Only for `trigger:`, never `env:` — `env:` allows any simple
        value, not a boolean condition, so it has no comparison shape to
        type-check. Catches comparisons between statically-incompatible types."""
        violations = trigger_type_violations(expression)
        if violations:
            raise ValueError(f"{context} ('{expression}'): {'; '.join(violations)}")

    @staticmethod
    def _validate_automaton_refs_exist(
        expression: str, referenced_projects: set[str], known_projects: dict[str, frozenset[str]], context: str
    ) -> None:
        """Whether the project/env key an automaton.* reference actually
        names exists at all. `known_projects` maps every *other*
        project's own project.id to its declared env key names."""
        unknown_projects = referenced_projects - known_projects.keys()
        if unknown_projects:
            raise ValueError(
                f"{context} references automaton.{', automaton.'.join(sorted(unknown_projects))} — "
                "not a known project.id."
            )
        for project_id, env_keys in trigger_automaton_env_refs(expression).items():
            declared = known_projects.get(project_id)
            if declared is None:
                continue  # already reported above as an unknown project
            unknown_keys = env_keys - declared
            if unknown_keys:
                raise ValueError(
                    f"{context} references automaton.{project_id}.env.{', '.join(sorted(unknown_keys))} — "
                    f"not declared in project '{project_id}''s own 'env' section."
                )

    def _actions_sanity_check(
        self, key: str, state: State, declared_states: set[str], registry: dict[str, dict[str, str]],
        known_projects: dict[str, frozenset[str]] | None = None,
    ):
        """`registry`: every valid identifier for this project, one set
        per namespace. `known_projects`: None skips the automaton.*
        existence check entirely."""
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
                referenced_projects = trigger_automaton_project_refs(action.trigger)
                if referenced_projects and action.target != state.key:
                    raise ValueError(
                        f"State {key}, action '{action.name}': trigger references automaton.* but this "
                        f"action isn't a self-loop (target '{action.target}' != state '{state.key}') — "
                        "automaton.* is only ever allowed in a self-loop action's own trigger."
                    )
                if known_projects is not None and referenced_projects:
                    self._validate_automaton_refs_exist(
                        action.trigger, referenced_projects, known_projects,
                        f"State {key}, action '{action.name}': trigger",
                    )
            if action.env:
                for env_key, expression in action.env.items():
                    if env_key not in registry.get("env", {}):
                        raise ValueError(
                            f"State {key}, action '{action.name}': env key '{env_key}' is not declared in "
                            "the project's own 'env' section — declare it there first."
                        )
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
        init_on_enter = raw_init_action.get("on-enter")
        self._validate_on_enter(init_on_enter, "init-action")
        init_action = Action(
            name="init-action",
            ui_label=raw_init_action.get("ui-label", "init-action"),
            ui_button="",
            target=raw_init_action["target"],
            action_prompt=raw_init_action["action-prompt"].strip() if raw_init_action.get("action-prompt") else None,
            on_enter=init_on_enter,
        )
        return init_action


    @staticmethod
    def _build_project_metadata(raw: dict) -> tuple[str | None, str | None, str | None, bool, bool]:
        """The optional top-level `project:` section — id/ui-label/
        ui-description/signal-tracking-on-ai-message/talk-enabled. `id` is
        what *other* projects reach this one as through automaton.*;
        global uniqueness is ProjectService's concern."""
        raw_project = raw.get("project")
        if raw_project is None:
            return None, None, None, False, True
        if not isinstance(raw_project, dict):
            raise ValueError(
                f"'project' must be a mapping of fields (id, ui-label, ui-description, "
                f"signal-tracking-on-ai-message, talk-enabled), got {type(raw_project).__name__}."
            )
        project_id = raw_project.get("id")
        if project_id is not None and (not isinstance(project_id, str) or not project_id.isidentifier()):
            raise ValueError(
                f"project.id {project_id!r} is not a valid identifier — letters, digits, and "
                "underscores only, and it can't start with a digit."
            )
        return (
            project_id, raw_project.get("ui-label"), raw_project.get("ui-description"),
            raw_project.get("signal-tracking-on-ai-message", False),
            raw_project.get("talk-enabled", True),
        )

    @staticmethod
    def read_declared_env_keys(index_yml_text: str) -> tuple[str | None, frozenset[str]]:
        """Reads only `project.id` and the top-level `env:` key names,
        never a full build — lets ProjectService validate automaton.*
        references without building every other project. Malformed id/env reports nothing."""
        raw = _yaml.load(index_yml_text)
        raw_project = raw.get("project")
        project_id = raw_project.get("id") if isinstance(raw_project, dict) else None
        if not isinstance(project_id, str) or not project_id.isidentifier():
            project_id = None
        raw_env = raw.get("env")
        env_keys = frozenset(raw_env.keys()) if isinstance(raw_env, dict) else frozenset()
        return project_id, env_keys

    def build(self, contents: dict, known_projects: dict[str, frozenset[str]] | None = None) -> Automaton:
        """`known_projects`: every *other* project's own project.id
        mapped to its declared env key names, for validating an
        automaton.<id>.env.<key> reference. None skips that check."""
        all_archives = self._convert_contents_to_archives(contents=contents)

        raw = _yaml.load(contents['index.yml'])

        project_id, project_ui_label, project_ui_description, autotracking_on_ai_message, talk_enabled = (
            self._build_project_metadata(raw)
        )

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

        raw_reactions = raw.get("reactions", {})
        if not isinstance(raw_reactions, dict):
            raise ValueError(f"'reactions' must be a mapping of reaction name -> fields, got {type(raw_reactions).__name__}.")

        reactions: dict[str, Reaction] = {}
        reaction_names_by_ui_label: dict[str, str] = {}
        for name, raw_reaction in raw_reactions.items():
            reaction = self._build_reaction(name, raw_reaction)
            existing_name = reaction_names_by_ui_label.get(reaction.ui_label)
            if existing_name is not None:
                raise ValueError(
                    f"Reactions '{existing_name}' and '{name}' both use ui-label "
                    f"'{reaction.ui_label}' — ui-label must be unique across all reactions."
                )
            reaction_names_by_ui_label[reaction.ui_label] = name
            reactions[name] = reaction

        raw_env_keys = raw.get("env", {})
        if not isinstance(raw_env_keys, dict):
            raise ValueError(f"'env' must be a mapping of env key -> fields, got {type(raw_env_keys).__name__}.")
        env_keys: dict[str, EnvKey] = {
            name: self._build_env_key(name, raw_env_key) for name, raw_env_key in raw_env_keys.items()
        }

        raw_states = raw["states"]
        if not isinstance(raw_states, dict):
            raise ValueError(f"'states' must be a mapping of state name -> fields, got {type(raw_states).__name__}.")
        if "" in raw_states:
            raise ValueError(
                "State '' is reserved for the implicit initial state (see init-action) "
                "and cannot be declared in 'states'."
            )

        # Pass 1: build every state/action first, no expression validation
        # yet — forward references to a state/signal/env key declared
        # elsewhere in the file are fine, so every state must exist first.
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

        # Pass 2: build the identifier registry, the single source
        # _actions_sanity_check validates every trigger/env: expression
        # against below.
        registry = build_registry(list(signals.values()), list(env_keys.values()))
        for key, state in states.items():
            context_key = init_action.name if key == "" else key
            self._actions_sanity_check(context_key, state, set(raw_states.keys()), registry, known_projects)

        # An env key's own `value` (its default) is a namespaced
        # expression exactly like an action's own env: one, so it gets
        # the same validation.
        for env_key in env_keys.values():
            if env_key.value:
                self._validate_namespaced_expression(
                    env_key.value, f"Env key '{env_key.name}': value", registry,
                )

        general_attachments = self._extract_required_archives(raw.get('attachments', []), all_archives, for_field="global")

        return Automaton(
            init_action=init_action,
            states=states,
            general_prompt=raw.get("general-prompt", ""),
            signals=list(signals.values()),
            reactions=list(reactions.values()),
            env_keys=list(env_keys.values()),
            general_attachments=general_attachments,
            attachments=all_archives,
            autotracking_on_ai_message=autotracking_on_ai_message,
            project_id=project_id,
            project_ui_label=project_ui_label,
            project_ui_description=project_ui_description,
            talk_enabled=talk_enabled,
        )
