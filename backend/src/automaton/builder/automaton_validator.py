from __future__ import annotations

import inspect

from automaton.builder.archive_resolver import ProjectArchives
from automaton.automaton import EnvKey, Source, State
from automaton.builder.build_cursor import BuildCursor
from automaton.identifier_registry import IdentifierRegistry
from automaton.trigger_expression_analyzer import TriggerExpressionAnalyzer
from metrics.metrics_framework import metric_names
from tracking.actuators import ChatNamespace, MAX_ATTACHMENT_READ_BYTES, TaskNamespace
from tracking.sources import READ_METHOD, WRITE_METHOD, driver_class_for

STATE_SOURCE_FIELDS = (
    ("ai-may-read-sources", READ_METHOD), ("ai-must-read-sources", READ_METHOD), ("ai-may-write-sources", WRITE_METHOD),
)


class AutomatonValidator:
    def __init__(self, cursor: BuildCursor) -> None:
        self._cursor = cursor

    def validate_env_key_default_order(self, env_keys: dict[str, EnvKey], raw_env_keys) -> None:
        all_names = set(env_keys.keys())
        declared_so_far: set[str] = set()
        for name, env_key in env_keys.items():
            self._cursor.at(self._cursor.line_of(raw_env_keys, name), f"env.{name}")
            if env_key.value:
                try:
                    referenced = TriggerExpressionAnalyzer.namespace_refs(env_key.value).get("env", set())
                except SyntaxError:
                    referenced = set()
                forward = (referenced & all_names) - declared_so_far
                if forward:
                    raise ValueError(
                        f"env key '{name}': default value references "
                        f"{', '.join(f'env.{ref}' for ref in sorted(forward))} before it's declared — "
                        "an env key's own default may only reference an earlier env key, never itself or a later one."
                    )
            declared_so_far.add(name)

    @staticmethod
    def supported_methods(source: Source) -> frozenset[str]:
        try:
            return driver_class_for(source.url).SUPPORTED_METHODS
        except (ValueError, KeyError):
            return frozenset()

    @classmethod
    def validate_namespaced_expression(
        cls, expression: str, context: str, registry: dict[str, dict[str, str]], sources: dict[str, Source],
        known_locals: frozenset[str] = frozenset(),
    ) -> None:
        try:
            namespace_refs = TriggerExpressionAnalyzer.namespace_refs(expression)
            bare_names = TriggerExpressionAnalyzer.bare_names(expression)
            source_refs = TriggerExpressionAnalyzer.source_refs(expression)
        except SyntaxError as exc:
            raise ValueError(f"{context} ('{expression}') is not a valid expression: {exc}") from exc

        unknown = set()
        for namespace, refs in namespace_refs.items():
            valid = registry.get(namespace, {}).keys()
            unknown |= {f"{namespace}.{n}" for n in refs - valid}
        unknown |= bare_names - metric_names() - known_locals
        read_on_a_source = False
        for source_name, methods in source_refs.items():
            source = sources.get(source_name)
            if source is None:
                unknown.add(f"source.{source_name}")
                continue
            unsupported = methods - cls.supported_methods(source)
            unknown |= {f"source.{source_name}.{m}" for m in unsupported}
            read_on_a_source = read_on_a_source or "read" in unsupported
        if unknown:
            message = f"{context} references undefined name(s): {', '.join(sorted(unknown))}"
            if read_on_a_source:
                message += " — a whole-file read is attachment.read(name)'s job (task only), not source.*."
            raise ValueError(message)

    @staticmethod
    def _validate_namespace_call_arity(expression: str, context: str, namespace: str, methods_class: type) -> None:
        """Shared by validate_task_arity/validate_chat_arity below: every
        `<namespace>.<method>(...)` call in `expression` must pass the
        same number of arguments its Python-side method (on
        `methods_class`) actually declares — checked generically off
        `inspect.signature` rather than one hand-written arity table per
        namespace."""
        for method_name, arg_count in TriggerExpressionAnalyzer.namespace_calls(expression, namespace):
            method = getattr(methods_class, method_name, None)
            if method is None:
                continue
            expected = len(inspect.signature(method).parameters) - 1
            if arg_count != expected:
                raise ValueError(
                    f"{context} ('{expression}'): {namespace}.{method_name}(...) takes {expected} "
                    f"argument(s), got {arg_count}"
                )

    @classmethod
    def validate_task_arity(cls, expression: str, context: str) -> None:
        cls._validate_namespace_call_arity(expression, context, "task", TaskNamespace)

    @classmethod
    def validate_chat_arity(cls, expression: str, context: str) -> None:
        cls._validate_namespace_call_arity(expression, context, "chat", ChatNamespace)

    @staticmethod
    def validate_attachment_read(expression: str, context: str, archives: ProjectArchives) -> None:
        violations = TriggerExpressionAnalyzer.attachment_read_violations(expression)
        if violations:
            raise ValueError(f"{context} ('{expression}'): {'; '.join(violations)}")
        for name in TriggerExpressionAnalyzer.attachment_read_names(expression):
            path = archives.require([name], context)[0]
            text = archives.text(path)
            if text is None:
                raise ValueError(
                    f"{context} ('{expression}'): attachment.read('{name}') targets a binary file — "
                    "only text files can be read this way."
                )
            size = len(text.encode("utf-8"))
            if size > MAX_ATTACHMENT_READ_BYTES:
                raise ValueError(
                    f"{context} ('{expression}'): attachment.read('{name}') is {size} bytes, over the "
                    f"{MAX_ATTACHMENT_READ_BYTES}-byte limit."
                )

    @classmethod
    def validate_task(
        cls, task: str | None, context: str, registry: dict[str, dict[str, str]], sources: dict[str, Source],
        archives: ProjectArchives,
    ) -> None:
        if not task:
            return
        try:
            statements = TriggerExpressionAnalyzer.task_statements(task)
        except SyntaxError as exc:
            raise ValueError(f"{context} ('{task}') is not valid task source: {exc}") from exc
        known_locals: set[str] = set()
        for line_number, statement in statements:
            line_context = f"{context}, task line {line_number}"
            assignment = TriggerExpressionAnalyzer.task_assignment(statement)
            target, expression = assignment if assignment is not None else (None, statement)
            if target is not None and (target in TriggerExpressionAnalyzer.RESERVED_NAMESPACES or target in metric_names()):
                raise ValueError(
                    f"{line_context} ('{statement}'): '{target}' is a reserved name "
                    "(a namespace or core metric) and can't be used as a task local variable."
                )
            cls.validate_namespaced_expression(expression, line_context, registry, sources, frozenset(known_locals))
            cls.validate_task_arity(expression, line_context)
            cls.validate_attachment_read(expression, line_context, archives)
            violations = TriggerExpressionAnalyzer.defer_violations(expression)
            if violations:
                raise ValueError(f"{line_context} ('{statement}'): {'; '.join(violations)}")
            if target is not None:
                known_locals.add(target)

    @classmethod
    def validate_on_exit(
        cls, on_exit: str | None, context: str, registry: dict[str, dict[str, str]], sources: dict[str, Source],
        env_keys: dict[str, EnvKey],
    ) -> None:
        """`on-exit` shares task's own statement splitting
        (TriggerExpressionAnalyzer.task_statements — same multi-line-
        call/'#'-comment handling), but the mixed grammar its own
        statements accept differs from task's own: each statement must
        be *either* an `env.<key> = expr` assignment
        (TriggerExpressionAnalyzer.on_exit_assignment), checked exactly
        like one of the declarative `env:` map's own entries (env key
        must already be declared, validate_env_key_type included), *or*
        a bare `chat.<method>(...)` call (TriggerExpressionAnalyzer.
        bare_namespace_call) — on-exit's own side effect, arity-checked
        the same way task's own namespaced calls are (see
        validate_chat_arity). `registry` here is expected to be the
        for_on_exit() view: `chat` visible, `task`/`attachment` excluded
        — on-exit can't call task.*'s own send_mail/whatsapp/defer/prompt,
        that's task's own job."""
        if not on_exit:
            return
        try:
            statements = TriggerExpressionAnalyzer.task_statements(on_exit)
        except SyntaxError as exc:
            raise ValueError(f"{context} ('{on_exit}') is not valid on-exit source: {exc}") from exc
        for line_number, statement in statements:
            line_context = f"{context}, on-exit line {line_number}"
            assignment = TriggerExpressionAnalyzer.on_exit_assignment(statement)
            if assignment is not None:
                env_key, expression = assignment
                if env_key not in registry.get("env", {}):
                    raise ValueError(
                        f"{line_context}: env key '{env_key}' is not declared in the project's own "
                        "'env' section — declare it there first."
                    )
                cls.validate_namespaced_expression(expression, line_context, registry, sources)
                cls.validate_env_key_type(env_keys[env_key], expression, line_context)
                continue
            if TriggerExpressionAnalyzer.bare_namespace_call(statement, "chat") is None:
                raise ValueError(
                    f"{line_context} ('{statement}'): on-exit only supports 'env.<key> = expr' assignments "
                    "or a bare 'chat.<method>(...)' call."
                )
            cls.validate_namespaced_expression(statement, line_context, registry, sources)
            cls.validate_chat_arity(statement, line_context)

    @staticmethod
    def validate_trigger_types(expression: str, context: str) -> None:
        violations = TriggerExpressionAnalyzer.type_violations(expression)
        if violations:
            raise ValueError(f"{context} ('{expression}'): {'; '.join(violations)}")

    @staticmethod
    def validate_automaton_refs_exist(
        expression: str, referenced_projects: set[str], known_projects: dict[str, frozenset[str]], context: str
    ) -> None:
        unknown_projects = referenced_projects - known_projects.keys()
        if unknown_projects:
            raise ValueError(
                f"{context} references automaton.{', automaton.'.join(sorted(unknown_projects))} — "
                "not a known project.id."
            )
        for project_id, env_keys in TriggerExpressionAnalyzer.automaton_env_refs(expression).items():
            declared = known_projects.get(project_id)
            if declared is None:
                continue
            unknown_keys = env_keys - declared
            if unknown_keys:
                raise ValueError(
                    f"{context} references automaton.{project_id}.env.{', '.join(sorted(unknown_keys))} — "
                    f"not declared in project '{project_id}''s own 'env' section."
                )

    def check_state(
        self, key: str, state: State, declared_states: set[str], registry: dict[str, dict[str, str]],
        env_keys: dict[str, EnvKey], sources: dict[str, Source], archives: ProjectArchives,
        known_projects: dict[str, frozenset[str]] | None = None,
    ) -> None:
        registry_for_triggers = IdentifierRegistry.for_triggers(registry)
        registry_for_task = IdentifierRegistry.for_task(registry)
        registry_for_on_exit = IdentifierRegistry.for_on_exit(registry)
        self._cursor.at(state.line, f"states.{key}")
        self.validate_state_sources(state, sources)
        self.validate_state_io(state, env_keys)
        for action in state.actions:
            self._cursor.at(action.line, f"states.{key}.actions.{action.name}")
            action_context = f"State {key}, action '{action.name}'"
            if action.target not in declared_states:
                raise ValueError(
                    f"State '{state.key}', action '{action.name}': "
                    f"target '{action.target}' is not a valid state"
                )
            if action.trigger:
                self.validate_namespaced_expression(
                    action.trigger, f"{action_context}: trigger", registry_for_triggers, sources,
                )
                self.validate_trigger_types(action.trigger, f"{action_context}: trigger")
                referenced_projects = TriggerExpressionAnalyzer.automaton_project_refs(action.trigger)
                if referenced_projects and action.target != state.key:
                    raise ValueError(
                        f"{action_context}: trigger references automaton.* but this "
                        f"action isn't a self-loop (target '{action.target}' != state '{state.key}') — "
                        "automaton.* is only ever allowed in a self-loop action's own trigger."
                    )
                if known_projects is not None and referenced_projects:
                    self.validate_automaton_refs_exist(
                        action.trigger, referenced_projects, known_projects, f"{action_context}: trigger",
                    )
            if action.env:
                for env_key, expression in action.env.items():
                    if env_key not in registry.get("env", {}):
                        raise ValueError(
                            f"{action_context}: env key '{env_key}' is not declared in "
                            "the project's own 'env' section — declare it there first."
                        )
                    self.validate_namespaced_expression(
                        expression, f"{action_context}: env expression for '{env_key}'",
                        registry_for_triggers, sources,
                    )
                    self.validate_env_key_type(env_keys[env_key], expression, action_context)
            if action.task:
                self.validate_task(action.task, action_context, registry_for_task, sources, archives)
            if action.on_exit:
                self.validate_on_exit(action.on_exit, action_context, registry_for_on_exit, sources, env_keys)

    def validate_state_io(self, state: State, env_keys: dict[str, EnvKey]) -> None:
        for field_name, names in (("input", state.input), ("output", state.output)):
            for name in names:
                env_key = env_keys.get(name)
                if env_key is None:
                    raise ValueError(
                        f"State '{state.key}': {field_name} '{name}' — 'env.{name}' is not "
                        "declared in the project's own 'env' section."
                    )
                if not env_key.ai_definition:
                    raise ValueError(
                        f"State '{state.key}': {field_name} '{name}' — env key '{name}' has no own "
                        "'ai-definition', required for a variable exposed to the model as input/output."
                    )

    def validate_state_sources(self, state: State, sources: dict[str, Source]) -> None:
        by_field = {
            "ai-may-read-sources": state.ai_may_read_sources,
            "ai-must-read-sources": state.ai_must_read_sources,
            "ai-may-write-sources": state.ai_may_write_sources,
        }
        for field_name, method in STATE_SOURCE_FIELDS:
            for source_name in by_field[field_name]:
                source = sources.get(source_name)
                if source is None:
                    raise ValueError(
                        f"State '{state.key}': {field_name} '{source_name}' — 'sources.{source_name}' is not "
                        "declared in the project's own 'sources:' section."
                    )
                if not source.ai_definition:
                    raise ValueError(
                        f"State '{state.key}': {field_name} '{source_name}' — source '{source_name}' has no "
                        "own 'ai-definition', required for a source exposed to the model as a tool."
                    )
                if (source.url or method == WRITE_METHOD) and method not in self.supported_methods(source):
                    raise ValueError(
                        f"State '{state.key}': {field_name} '{source_name}' references undefined name(s): "
                        f"source.{source_name}.{method}"
                    )

    @staticmethod
    def validate_env_key_type(declared: EnvKey, expression: str, context: str) -> None:
        if not declared.value:
            return
        declared_kind = TriggerExpressionAnalyzer.expression_kind(declared.value)
        written_kind = TriggerExpressionAnalyzer.expression_kind(expression)
        if declared_kind is None or written_kind is None or declared_kind == written_kind:
            return
        raise ValueError(
            f"{context}: env expression for '{declared.name}' ('{expression}') is a {written_kind}, but "
            f"'{declared.name}' was declared as a {declared_kind} (its own 'value' default) — an env "
            "key's type can't change once declared."
        )
