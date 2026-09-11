from automaton.automaton import (
    Action, EnvKey, Automaton, Reaction, Signal, Source, State,
)
from automaton.builder.archive_resolver import ProjectArchives
from automaton.builder.automaton_validator import AutomatonValidator, STATE_SOURCE_FIELDS
from automaton.builder.build_cursor import BuildCursor
from automaton.build_error import AutomatonBuildError
from automaton.identifier_registry import IdentifierRegistry
from automaton.builder.project_metadata import ProjectMetadata, load_yaml, peek_declared_revision, read_declared_env_keys
from typing import Any
from system.logging_factory import LoggerFactory
from metrics.metrics_framework import metric_names
from tracking.sources import SOURCE_DRIVERS
from tracking.sources.url import parse_source_url

from ruamel.yaml.error import YAMLError

logger = LoggerFactory.get_logger(__name__)

VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

LEGACY_STATE_SOURCE_FIELDS = {
    "tools": "ai-may-read-sources",
    "ai-may-query-sources": "ai-may-read-sources",
    "ai-must-query-sources": "ai-must-read-sources",
}


class AutomatonBuilder(object):
    def __init__(self) -> None:
        self._cursor = BuildCursor()
        self._validator = AutomatonValidator(self._cursor)

    def _at(self, line: int | None, section: str) -> None:
        self._cursor.at(line, section)

    @staticmethod
    def _line_of(parent, key: str) -> int | None:
        return BuildCursor.line_of(parent, key)

    def _build_signal(self, name, raw_signal: dict, archives: ProjectArchives) -> Signal:
        return Signal(
            name=name,
            ui_label=raw_signal.get("ui-label", name),
            ui_description=raw_signal["ui-description"].strip() if raw_signal.get("ui-description") else raw_signal["definition"].strip(),
            definition=raw_signal["definition"].strip(),
            attachments=archives.require(raw_signal.get("attachments", []), f"signal '{name}'"),
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
        raw_env_key = raw_env_key or {}
        raw_value = raw_env_key.get("value", "")
        value = raw_value if isinstance(raw_value, str) else str(raw_value)
        raw_description = raw_env_key.get("ui-description")
        raw_ai_definition = raw_env_key.get("ai-definition")
        return EnvKey(
            name=name,
            value=value.strip(),
            ui_description=raw_description.strip() if raw_description else None,
            ai_definition=raw_ai_definition.strip() if isinstance(raw_ai_definition, str) and raw_ai_definition.strip() else None,
        )

    def _build_source(self, name: str, raw_source: dict, archives: ProjectArchives) -> Source:
        raw_source = raw_source or {}
        url = raw_source.get("url") or ""
        if url:
            try:
                scheme, path = parse_source_url(url)
            except ValueError as exc:
                raise ValueError(f"Source '{name}': {exc}") from exc
            if scheme not in SOURCE_DRIVERS:
                raise ValueError(
                    f"Source '{name}': url scheme '{scheme}' must be one of: {', '.join(sorted(SOURCE_DRIVERS))}."
                )
            if scheme == "avance" and archives.find(path, f"source '{name}'") is None:
                archives.declare_missing(path)
        raw_ai_definition = raw_source.get("ai-definition")
        return Source(
            name=name,
            url=url,
            ui_label=raw_source.get("ui-label", name),
            ui_description=raw_source.get("ui-description"),
            ai_definition=raw_ai_definition.strip() if raw_ai_definition else None,
        )

    @staticmethod
    def _build_action_env(raw_env: Any, action_name: str) -> dict[str, str] | None:
        if not raw_env:
            return None
        if not isinstance(raw_env, dict):
            raise ValueError(
                f"Action '{action_name}': 'env' must be a mapping of key -> expression, "
                f"got {type(raw_env).__name__}."
            )
        return {key: value if isinstance(value, str) else str(value) for key, value in raw_env.items()}

    def _build_action(self, key: str, raw_action: dict, archives: ProjectArchives) -> Action:
        task = raw_action.get("task")
        on_exit = raw_action.get("on-exit")
        line = BuildCursor.own_line(raw_action)
        self._at(line, f"states.{key}.actions.{raw_action.get('name', '?')}")
        return Action(
            name=raw_action["name"],
            ui_description=raw_action.get("ui-description"),
            ui_label=raw_action.get("ui-label") or raw_action["name"],
            ui_button=raw_action.get("ui-button") or raw_action.get("ui-label") or raw_action["name"],
            target=raw_action.get("target", key),
            trigger=raw_action.get("trigger"),
            task=task,
            on_exit=on_exit,
            env=self._build_action_env(raw_action.get("env"), raw_action["name"]),
            line=line,
        )

    def _build_state_source_lists(self, key: str, raw_state: dict) -> dict[str, list[str]]:
        for legacy_field, replacement in LEGACY_STATE_SOURCE_FIELDS.items():
            if legacy_field in raw_state:
                raise ValueError(
                    f"State '{key}': '{legacy_field}' is no longer a valid field — use '{replacement}' instead "
                    "('ai-may-read-sources': the model decides whether to call a source's reads; "
                    "'ai-must-read-sources': forced once per entry into this state; "
                    "'ai-may-write-sources': the model may call a source's update)."
                )
        raw_source_lists: dict[str, list[str]] = {}
        for field_name, _method in STATE_SOURCE_FIELDS:
            raw_list = raw_state.get(field_name, [])
            if not isinstance(raw_list, list) or not all(isinstance(t, str) for t in raw_list):
                raise ValueError(f"State '{key}': '{field_name}' must be a list of source names if present.")
            raw_source_lists[field_name] = list(raw_list)
        overlap = set(raw_source_lists["ai-may-read-sources"]) & set(raw_source_lists["ai-must-read-sources"])
        if overlap:
            raise ValueError(
                f"State '{key}': {', '.join(sorted(overlap))} declared in both 'ai-may-read-sources' "
                "and 'ai-must-read-sources' — a source can only be in one."
            )
        return raw_source_lists

    @staticmethod
    def _build_variable_name_list(key: str, raw_state: dict, field_name: str) -> tuple[str, ...]:
        """Parses `input`/`output` — a plain list of this automaton's own
        `env:` variable names; existence (each name is actually declared
        in `env:`) is checked later, once every state is built (see
        AutomatonValidator.validate_state_io), same staging as
        validate_state_sources for ai-may-read-sources et al."""
        raw_list = raw_state.get(field_name, [])
        if not isinstance(raw_list, list) or not all(isinstance(n, str) for n in raw_list):
            raise ValueError(f"State '{key}': '{field_name}' must be a list of env variable names if present.")
        return tuple(raw_list)

    def _build_state(self, key: str, raw_state: dict, archives: ProjectArchives, line: int | None = None) -> State:
        self._at(line, f"states.{key}")
        actions: list[Action] = []
        action_names_by_ui_label: dict[str, str] = {}
        for raw_action in raw_state.get("actions", []):
            action = self._build_action(key, raw_action, archives)
            existing_name = action_names_by_ui_label.get(action.ui_label)
            if existing_name is not None:
                raise ValueError(
                    f"State '{key}': actions '{existing_name}' and '{action.name}' both use "
                    f"ui-label '{action.ui_label}' — ui-label must be unique within a state."
                )
            action_names_by_ui_label[action.ui_label] = action.name
            actions.append(action)
        self._at(line, f"states.{key}")
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

        raw_source_lists = self._build_state_source_lists(key, raw_state)
        input_names = self._build_variable_name_list(key, raw_state, "input")
        output_names = self._build_variable_name_list(key, raw_state, "output")

        return State(
            key=key,
            ui_label=raw_state.get("ui-label", key),
            final=len(actions) == 0,
            ui_description=raw_state["ui-description"].strip() if raw_state.get("ui-description") else None,
            contextual_prompt=contextual_prompt.strip() if contextual_prompt else None,
            actions=actions,
            fixed_message=fixed_message.strip() if fixed_message else None,
            transition_log_level=transition_log_level,
            attachments=archives.require(raw_state.get("attachments", []), f"state '{key}'"),
            history_cutoff=raw_state.get("history-cutoff", False),
            chat_enabled=raw_state.get("chat-enabled", True),
            reactions_enabled=raw_state.get("reactions-enabled", False),
            ai_may_read_sources=tuple(raw_source_lists["ai-may-read-sources"]),
            ai_must_read_sources=tuple(raw_source_lists["ai-must-read-sources"]),
            ai_may_write_sources=tuple(raw_source_lists["ai-may-write-sources"]),
            input=input_names,
            output=output_names,
            line=line,
        )

    def _build_init_action(self, raw: dict, env_keys: dict[str, EnvKey]) -> Action:
        line = self._line_of(raw, "init-action")
        self._at(line, "init-action")
        raw_init_action = raw.get("init-action")
        if not isinstance(raw_init_action, dict) or not raw_init_action.get("target"):
            raise ValueError(
                "'init-action' is required and must be a mapping with at least a 'target' "
                "field — the project's real starting state."
            )
        # XXX Compiled automaton requirement - do not touch.
        # XXX Every declared key, not just the ones with a default: a
        # XXX key left out here is never written at session open (see
        # XXX TurnService._apply_declared_env_defaults), so it is absent
        # XXX from the evaluation scope and reading it raised KeyError
        # XXX through simpleeval's attribute-to-item fallback, silently
        # XXX failing the whole expression — a trigger like
        # XXX `env.pnr != ''` could never fire. Generated Python has no
        # XXX equivalent of that accident and must not imitate it. A key
        # XXX with no declared default gets "''", the expression for an
        # XXX empty string: these are expression sources, evaluated once
        # XXX at session open, never literals.
        env = {name: (env_key.value or "''") for name, env_key in env_keys.items()}
        env.update(self._build_action_env(raw_init_action.get("env"), "init-action") or {})
        return Action(
            name="init-action",
            ui_description=raw_init_action.get("ui-description"),
            ui_label=raw_init_action.get("ui-label", "init-action"),
            ui_button="",
            target=raw_init_action["target"],
            task=raw_init_action.get("task"),
            on_exit=raw_init_action.get("on-exit"),
            env=env or None,
            line=line,
        )

    @staticmethod
    def read_declared_env_keys(index_yml_text: str) -> tuple[str | None, str | None, frozenset[str]]:
        return read_declared_env_keys(index_yml_text)

    @staticmethod
    def peek_declared_revision(index_yml_text: str) -> int | None:
        return peek_declared_revision(index_yml_text)

    def build(
        self, contents: dict, known_projects: dict[str, frozenset[str]] | None = None, *,
        legacy_project_id: str | None = None,
    ) -> Automaton:
        try:
            return self._build(contents, known_projects, legacy_project_id=legacy_project_id)
        except AutomatonBuildError:
            raise
        except YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            line = mark.line if mark is not None else self._cursor.line
            raise AutomatonBuildError(str(exc), line=line, section=self._cursor.section) from exc
        except ValueError as exc:
            raise AutomatonBuildError(str(exc), line=self._cursor.line, section=self._cursor.section) from exc

    def _require_mapping_section(self, raw: dict, section: str, item_label: str) -> dict:
        raw_section = raw.get(section, {})
        if not isinstance(raw_section, dict):
            self._at(self._line_of(raw, section), section)
            raise ValueError(f"'{section}' must be a mapping of {item_label} -> fields, got {type(raw_section).__name__}.")
        return raw_section

    def _check_unique_ui_labels(self, items: dict, raw_section: dict, section: str, label_scope: str) -> None:
        names_by_ui_label: dict[str, str] = {}
        for name, item in items.items():
            existing_name = names_by_ui_label.get(item.ui_label)
            if existing_name is not None:
                self._at(self._line_of(raw_section, name), f"{section}.{name}")
                raise ValueError(
                    f"{label_scope} '{existing_name}' and '{name}' both use ui-label "
                    f"'{item.ui_label}' — ui-label must be unique across all {section}."
                )
            names_by_ui_label[item.ui_label] = name

    def _build(
        self, contents: dict, known_projects: dict[str, frozenset[str]] | None, *,
        legacy_project_id: str | None,
    ) -> Automaton:
        archives = ProjectArchives(contents)

        raw = load_yaml(contents['index.yml'])
        if not isinstance(raw, dict):
            raise ValueError(f"index.yml must be a YAML mapping at the top level, got {type(raw).__name__}.")

        self._at(self._line_of(raw, "project"), "project")
        metadata = ProjectMetadata.from_raw(raw, legacy_project_id=legacy_project_id)
        for warning in metadata.service_warnings:
            self._cursor.warn(warning)

        raw_signals = self._require_mapping_section(raw, "signals", "signal name")
        signals: dict[str, Signal] = {}
        for name, raw_signal in raw_signals.items():
            self._at(self._line_of(raw_signals, name), f"signals.{name}")
            signals[name] = self._build_signal(name, raw_signal, archives)
        self._check_unique_ui_labels(signals, raw_signals, "signals", "Signals")

        reserved_names = set(signals.keys()) & metric_names()
        if reserved_names:
            self._at(None, "signals")
            raise ValueError(
                "Signal name(s) reserved for core metrics (see metrics_framework) cannot be "
                f"reused as signal names: {', '.join(sorted(reserved_names))}"
            )

        raw_reactions = self._require_mapping_section(raw, "reactions", "reaction name")
        reactions: dict[str, Reaction] = {}
        for name, raw_reaction in raw_reactions.items():
            self._at(self._line_of(raw_reactions, name), f"reactions.{name}")
            reactions[name] = self._build_reaction(name, raw_reaction)
        self._check_unique_ui_labels(reactions, raw_reactions, "reactions", "Reactions")

        raw_env_keys = self._require_mapping_section(raw, "env", "env key")
        env_keys: dict[str, EnvKey] = {}
        for name, raw_env_key in raw_env_keys.items():
            self._at(self._line_of(raw_env_keys, name), f"env.{name}")
            env_keys[name] = self._build_env_key(name, raw_env_key)

        raw_sources = self._require_mapping_section(raw, "sources", "source name")
        sources: dict[str, Source] = {}
        for name, raw_source in raw_sources.items():
            self._at(self._line_of(raw_sources, name), f"sources.{name}")
            sources[name] = self._build_source(name, raw_source, archives)
        self._validator.validate_env_key_default_order(env_keys, raw_env_keys)

        raw_states = raw["states"]
        if not isinstance(raw_states, dict):
            self._at(self._line_of(raw, "states"), "states")
            raise ValueError(f"'states' must be a mapping of state name -> fields, got {type(raw_states).__name__}.")
        if "" in raw_states:
            self._at(self._line_of(raw_states, ""), "states")
            raise ValueError(
                "State '' is reserved for the implicit initial state (see init-action) "
                "and cannot be declared in 'states'."
            )

        init_action = self._build_init_action(raw, env_keys)
        states: dict[str, State] = {}
        states[""] = State(key="", ui_label="", final=False, ui_description="", actions=[init_action])

        state_keys_by_ui_label: dict[str, str] = {}
        for key, raw_state in raw_states.items():
            state_line = self._line_of(raw_states, key)
            self._at(state_line, f"states.{key}")
            if not isinstance(raw_state, dict):
                raise ValueError(
                    f"State '{key}': expected a mapping of fields (ui-label, ui-description, "
                    f"actions, ...), got {type(raw_state).__name__} instead. This usually "
                    "means a field meant to belong to a state (e.g. 'actions') was "
                    "indented as a sibling of the state's key rather than nested under it, "
                    "so YAML parsed it as its own separate state."
                )

            states[key] = self._build_state(key, raw_state, archives, line=state_line)
            existing_key = state_keys_by_ui_label.get(states[key].ui_label)
            if existing_key is not None:
                raise ValueError(
                    f"States '{existing_key}' and '{key}' both use ui-label "
                    f"'{states[key].ui_label}' — ui-label must be unique across all states."
                )
            state_keys_by_ui_label[states[key].ui_label] = key

        registry = IdentifierRegistry.build(list(signals.values()), list(env_keys.values()))
        for key, state in states.items():
            context_key = init_action.name if key == "" else key
            self._validator.check_state(
                context_key, state, set(raw_states.keys()), registry, env_keys, sources, archives, known_projects,
            )

        general_attachments = archives.require(raw.get('attachments', []), for_field="global")

        return Automaton(
            init_action=init_action,
            states=states,
            general_prompt=raw.get("general-prompt", ""),
            signals=list(signals.values()),
            reactions=list(reactions.values()),
            env_keys=list(env_keys.values()),
            sources=list(sources.values()),
            general_attachments=general_attachments,
            autotracking_on_ai_message=metadata.autotracking_on_ai_message,
            project_id=metadata.project_id,
            project_family=metadata.family,
            project_revision=metadata.revision,
            project_ui_label=metadata.ui_label,
            project_ui_description=metadata.ui_description,
            project_services=metadata.services.as_raw(),
            new_session_strategy=metadata.new_session_strategy,
            build_warnings=self._cursor.warnings,
        )
