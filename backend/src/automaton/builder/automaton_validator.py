from __future__ import annotations

import inspect

from automaton.builder.archive_resolver import ProjectArchives
from automaton.automaton import EnvKey, Source, State
from automaton.builder.build_cursor import BuildCursor
from automaton.choice_namespace import list_key_names
from automaton.core import TASK_FUNCTION_NAMES, TRIGGER_FUNCTION_NAMES, AttachmentTemplate
from automaton.env_types import STORED_ENV_TYPES
from automaton.file_types import attachment_doc_id_for, media_doc_id_for
from automaton.identifier_registry import IdentifierRegistry
from automaton.input_processor_kind import INPUT_PROCESSOR_KINDS
from automaton.on_exit_expression_analyzer import OnExitExpressionAnalyzer
from automaton.trigger_expression_analyzer import TriggerExpressionAnalyzer
from automaton.trigger_namespaces import TriggerNamespaces
from metrics.metrics_framework import metric_names
from tracking.actuators import AttachmentDoc, ChatNamespace, DriveNamespace, MAX_ATTACHMENT_READ_BYTES, MediaDoc, TaskNamespace
from tracking.sources import READ_METHOD, driver_class_for

STATE_SOURCE_FIELDS = (
    ("ai-may-read-sources", READ_METHOD), ("ai-must-read-sources", READ_METHOD),
)


class AutomatonValidator:
    def __init__(self, cursor: BuildCursor) -> None:
        self._cursor = cursor

    @staticmethod
    def supported_methods(source: Source) -> frozenset[str]:
        try:
            return driver_class_for(source.url).SUPPORTED_METHODS
        except (ValueError, KeyError):
            return frozenset()

    @classmethod
    def validate_namespaced_expression(
        cls, expression: str, context: str, registry: dict[str, dict[str, str]], sources: dict[str, Source],
        known_locals: frozenset[str] = frozenset(), namespaces: frozenset[str] = frozenset(),
        known_builtins: frozenset[str] = frozenset(), media_doc_ids: frozenset[str] = frozenset(),
        attachment_doc_ids: frozenset[str] = frozenset(),
    ) -> None:
        try:
            namespace_refs = TriggerExpressionAnalyzer.namespace_refs(expression)
            bare_names = TriggerExpressionAnalyzer.bare_names(expression, namespaces)
            source_refs = TriggerExpressionAnalyzer.source_refs(expression)
            media_refs = TriggerExpressionAnalyzer.media_refs(expression)
            attachment_refs = TriggerExpressionAnalyzer.attachment_refs(expression)
        except SyntaxError as exc:
            raise ValueError(f"{context} ('{expression}') is not a valid expression: {exc}") from exc

        unknown = set()
        for namespace, refs in namespace_refs.items():
            valid = registry.get(namespace, {}).keys()
            unknown |= {f"{namespace}.{n}" for n in refs - valid}
        unknown |= bare_names - metric_names() - known_locals - known_builtins
        read_on_a_source = False
        for source_name, methods in source_refs.items():
            source = sources.get(source_name)
            if source is None:
                unknown.add(f"source.{source_name}")
                continue
            unsupported = methods - cls.supported_methods(source)
            unknown |= {f"source.{source_name}.{m}" for m in unsupported}
            read_on_a_source = read_on_a_source or "read" in unsupported
        for doc_id, methods in media_refs.items():
            if doc_id not in media_doc_ids:
                unknown.add(f"media.{doc_id}")
                continue
            unknown |= {f"media.{doc_id}.{m}" for m in methods - {"url"}}
        for doc_id, methods in attachment_refs.items():
            if doc_id not in attachment_doc_ids:
                unknown.add(f"attachment.{doc_id}")
                continue
            unknown |= {f"attachment.{doc_id}.{m}" for m in methods - {"read", "render"}}
        if unknown:
            message = f"{context} references undefined name(s): {', '.join(sorted(unknown))}"
            if read_on_a_source:
                message += " — a whole-file read is attachment.<name>.read()'s job (on-exit/task only), not source.*."
            raise ValueError(message)
        cls.validate_merged_string_arguments(expression, context)
        cls.validate_source_call_arguments(expression, context, sources)
        cls.validate_media_call_arguments(expression, context)
        cls.validate_expression_types(expression, context)

    @staticmethod
    def validate_merged_string_arguments(expression: str, context: str) -> None:
        merged = TriggerExpressionAnalyzer.merged_string_arguments(expression)
        if merged:
            raise ValueError(
                f"{context} ('{expression}'): {merged[0]} is two string literals with no comma between them — "
                "Python joins them into a single argument. Separate them with a comma."
            )

    @classmethod
    def validate_source_call_arguments(cls, expression: str, context: str, sources: dict[str, Source]) -> None:
        """Every `source.<name>.<method>(...)` call must bind to the
        driver method's own signature — same idea as
        _validate_namespace_call_arity, but through inspect's own bind,
        since a driver method takes *strings and keyword-only arguments
        a fixed count can't describe. The message is inspect's own: it
        names the missing argument."""
        for source_name, method_name, positional, keywords, unpacks in TriggerExpressionAnalyzer.source_calls(expression):
            source = sources.get(source_name)
            if source is None or unpacks:
                continue
            try:
                method = getattr(driver_class_for(source.url), method_name)
            except (ValueError, KeyError, AttributeError):
                continue
            try:
                inspect.signature(method).bind(None, *([None] * positional), **{name: None for name in keywords})
            except TypeError as exc:
                raise ValueError(
                    f"{context} ('{expression}'): source.{source_name}.{method_name}(...) {exc} — "
                    f"expected source.{source_name}.{method_name}{cls._signature_text(method)}"
                ) from exc

    @classmethod
    def validate_media_call_arguments(cls, expression: str, context: str) -> None:
        """Every `media.<doc_id>.<method>(...)` and
        `attachment.<doc_id>.<method>(...)` call must bind to its doc
        class's own method signature — same idea as
        validate_source_call_arguments, against the one fixed class every
        doc id's namespace object actually is (see tracking.actuators)."""
        calls = [
            (namespace, doc_class, call)
            for namespace, doc_class, found in (
                ("media", MediaDoc, TriggerExpressionAnalyzer.media_calls(expression)),
                ("attachment", AttachmentDoc, TriggerExpressionAnalyzer.attachment_calls(expression)),
            )
            for call in found
        ]
        for namespace, doc_class, (doc_id, method_name, positional, keywords, unpacks) in calls:
            if unpacks:
                continue
            method = getattr(doc_class, method_name, None)
            if method is None:
                continue
            try:
                inspect.signature(method).bind(None, *([None] * positional), **{name: None for name in keywords})
            except TypeError as exc:
                raise ValueError(
                    f"{context} ('{expression}'): {namespace}.{doc_id}.{method_name}(...) {exc} — "
                    f"expected {namespace}.{doc_id}.{method_name}{cls._signature_text(method)}"
                ) from exc

    @staticmethod
    def _signature_text(method) -> str:
        parameters = list(inspect.signature(method).parameters.values())[1:]
        return "(" + ", ".join(str(parameter).split(":")[0].split("=")[0] for parameter in parameters) + ")"

    @classmethod
    def _validate_namespace_call_arity(
        cls, expression: str, context: str, namespace: str, methods_class: type,
    ) -> None:
        """Shared by validate_task_arity/validate_chat_arity below: every
        `<namespace>.<method>(...)` call in `expression` must bind to its
        Python-side method (on `methods_class`) — through inspect's own
        bind, exactly like validate_source_call_arguments, so a method
        taking *args and keyword-only arguments (chat.chart) is described
        by its own signature rather than by a count that cannot express
        one. The message is inspect's own: it names what is missing."""
        for method_name, positional, keywords, unpacks in TriggerExpressionAnalyzer.namespace_calls(
            expression, namespace,
        ):
            method = getattr(methods_class, method_name, None)
            if method is None or unpacks:
                continue
            try:
                inspect.signature(method).bind(None, *([None] * positional), **{name: None for name in keywords})
            except TypeError as exc:
                raise ValueError(
                    f"{context} ('{expression}'): {namespace}.{method_name}(...) {exc} — "
                    f"expected {namespace}.{method_name}{cls._signature_text(method)}"
                ) from exc

    @classmethod
    def validate_task_arity(cls, expression: str, context: str) -> None:
        cls._validate_namespace_call_arity(expression, context, "task", TaskNamespace)

    @classmethod
    def validate_chat_arity(cls, expression: str, context: str) -> None:
        cls._validate_namespace_call_arity(expression, context, "chat", ChatNamespace)

    @classmethod
    def validate_drive_arity(cls, expression: str, context: str) -> None:
        cls._validate_namespace_call_arity(expression, context, "drive", DriveNamespace)

    @staticmethod
    def attachment_docs(archives: ProjectArchives) -> dict[str, list[str]]:
        docs: dict[str, list[str]] = {}
        for name in archives.names():
            for doc_id in filter(None, [attachment_doc_id_for(name)]):
                docs.setdefault(doc_id, []).append(name)
        return docs

    @classmethod
    def validate_script_expression(
        cls, expression: str, context: str, archives: ProjectArchives, registry: dict[str, dict[str, str]],
        sources: dict[str, Source], known_locals: frozenset[str], namespaces: frozenset[str] = frozenset(),
        media_doc_ids: frozenset[str] = frozenset(),
    ) -> None:
        """One task/on-exit expression: every name it reads, then every
        `attachment.<doc_id>` it reaches — the file must be one, text, under
        the size limit, and a rendered one may only hold expressions this
        very line could have written in its place."""
        docs = cls.attachment_docs(archives)
        cls.validate_namespaced_expression(
            expression, context, registry, sources, known_locals, namespaces, TASK_FUNCTION_NAMES, media_doc_ids,
            frozenset(docs),
        )
        for doc_id, methods in TriggerExpressionAnalyzer.attachment_refs(expression).items():
            paths = docs.get(doc_id, [])
            if len(paths) > 1:
                raise ValueError(
                    f"{context} ('{expression}'): attachment.{doc_id} could be any of {', '.join(sorted(paths))} — "
                    "rename all but one of them."
                )
            text = archives.text(paths[0])
            if text is None:
                raise ValueError(
                    f"{context} ('{expression}'): attachment.{doc_id} is '{paths[0]}', a binary file — "
                    "only text files can be read this way."
                )
            size = len(text.encode("utf-8"))
            if size > MAX_ATTACHMENT_READ_BYTES:
                raise ValueError(
                    f"{context} ('{expression}'): attachment.{doc_id} is {size} bytes, over the "
                    f"{MAX_ATTACHMENT_READ_BYTES}-byte limit."
                )
            for placeholder in AttachmentTemplate(text).expressions() if "render" in methods else ():
                cls.validate_namespaced_expression(
                    placeholder, f"{context}, attachment.{doc_id}.render()", registry, sources, known_locals,
                    namespaces, TASK_FUNCTION_NAMES, media_doc_ids, frozenset(docs),
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
            cls.validate_script_expression(expression, line_context, archives, registry, sources, frozenset(known_locals))
            cls.validate_task_arity(expression, line_context)
            cls.validate_drive_arity(expression, line_context)
            violations = TriggerExpressionAnalyzer.defer_violations(expression)
            if violations:
                raise ValueError(f"{line_context} ('{statement}'): {'; '.join(violations)}")
            if target is not None:
                known_locals.add(target)

    @classmethod
    def validate_on_exit(
        cls, on_exit: str | None, context: str, registry: dict[str, dict[str, str]], sources: dict[str, Source],
        env_keys: dict[str, EnvKey], archives: ProjectArchives, namespaces: frozenset[str],
    ) -> None:
        """`on-exit` shares task's own statement splitting
        (TriggerExpressionAnalyzer.task_statements — same multi-line-
        call/'#'-comment handling), but the mixed grammar its own
        statements accept differs from task's own: each statement must
        be an `env.<key> = expr` assignment
        (TriggerExpressionAnalyzer.on_exit_assignment), checked exactly
        like one of the declarative `env:` map's own entries (env key
        must already be declared, validate_env_key_type included), a
        `name = expr` local (TriggerExpressionAnalyzer.task_assignment,
        under task's own rules: no reserved name, readable only by a
        later line), or a bare `chat.<method>(...)` call
        (TriggerExpressionAnalyzer.bare_namespace_call) — on-exit's own
        side effect, arity-checked the same way task's own namespaced
        calls are (see validate_chat_arity). `registry` here is expected
        to be the for_on_exit() view: `chat` visible, `task` excluded
        — on-exit can't call task.*'s own send_mail/whatsapp/defer/prompt,
        that's task's own job."""
        if not on_exit:
            return
        try:
            statements = TriggerExpressionAnalyzer.task_statements(on_exit)
        except SyntaxError as exc:
            raise ValueError(f"{context} ('{on_exit}') is not valid on-exit source: {exc}") from exc
        media_doc_ids = frozenset(
            doc_id for name in archives.names() if (doc_id := media_doc_id_for(name)) is not None
        )
        cls._validate_on_exit_statements(
            statements, context, registry, sources, env_keys, archives, namespaces, media_doc_ids, set(),
        )

    @classmethod
    def _validate_on_exit_statements(
        cls, statements: list[tuple[int, str]], context: str, registry: dict[str, dict[str, str]],
        sources: dict[str, Source], env_keys: dict[str, EnvKey], archives: ProjectArchives,
        namespaces: frozenset[str], media_doc_ids: frozenset[str], known_locals: set[str],
    ) -> None:
        for line_number, statement in statements:
            line_context = f"{context}, on-exit line {line_number}"
            branches = OnExitExpressionAnalyzer.if_branches(statement, line_number)
            if branches is not None:
                branch_locals: list[set[str]] = []
                for condition, body in branches:
                    if condition is not None:
                        cls.validate_script_expression(
                            condition, line_context, archives, registry, sources, frozenset(known_locals),
                            namespaces, media_doc_ids,
                        )
                        cls.validate_expression_types(condition, line_context)
                    scoped = set(known_locals)
                    cls._validate_on_exit_statements(
                        body, context, registry, sources, env_keys, archives, namespaces, media_doc_ids, scoped,
                    )
                    branch_locals.append(scoped)
                if branches[-1][0] is None:
                    known_locals |= set.intersection(*branch_locals)
                continue
            assignment = TriggerExpressionAnalyzer.on_exit_assignment(statement)
            if assignment is not None:
                env_key, expression = assignment
                if env_key not in registry.get("env", {}):
                    raise ValueError(
                        f"{line_context}: env key '{env_key}' is not declared in the project's own "
                        "'env' section — declare it there first."
                    )
                cls.validate_script_expression(
                    expression, line_context, archives, registry, sources, frozenset(known_locals), namespaces, media_doc_ids,
                )
                cls.validate_env_key_type(env_keys[env_key], expression, line_context)
                continue
            local = TriggerExpressionAnalyzer.task_assignment(statement)
            if local is not None:
                target, expression = local
                if target in TriggerExpressionAnalyzer.RESERVED_NAMESPACES or target in metric_names():
                    raise ValueError(
                        f"{line_context} ('{statement}'): '{target}' is a reserved name "
                        "(a namespace or core metric) and can't be used as an on-exit local variable."
                    )
                cls.validate_script_expression(
                    expression, line_context, archives, registry, sources, frozenset(known_locals), namespaces, media_doc_ids,
                )
                known_locals.add(target)
                continue
            if TriggerExpressionAnalyzer.bare_namespace_call(statement, "chat") is None:
                raise ValueError(
                    f"{line_context} ('{statement}'): on-exit only supports 'env.<key> = expr' assignments, "
                    "'name = expr' locals, a bare 'chat.<method>(...)' call, or an 'if' of them."
                )
            if TriggerExpressionAnalyzer.bare_namespace_call(statement, "chat") == "bind_env":
                cls.validate_env_binding(statement, line_context, env_keys)
                continue
            cls.validate_script_expression(
                statement, line_context, archives, registry, sources, frozenset(known_locals), namespaces, media_doc_ids,
            )
            cls.validate_chat_arity(statement, line_context)

    @staticmethod
    def validate_env_binding(statement: str, context: str, env_keys: dict[str, EnvKey]) -> None:
        env_key = TriggerExpressionAnalyzer.env_binding(statement)
        if env_key is None:
            raise ValueError(f"{context} ('{statement}'): chat.bind_env takes exactly one env.<key>, e.g. chat.bind_env(env.mood).")
        if env_key not in env_keys:
            raise ValueError(
                f"{context} ('{statement}'): env key '{env_key}' is not declared in the project's own "
                "'env' section — declare it there first."
            )
        if env_key in list_key_names(env_keys):
            raise ValueError(f"{context} ('{statement}'): '{env_key}' is a list, and a list can't be bound with chat.bind_env.")

    @staticmethod
    def validate_expression_types(expression: str, context: str) -> None:
        try:
            violations = (
                TriggerExpressionAnalyzer.type_violations(expression)
                + TriggerExpressionAnalyzer.signal_domain_violations(expression)
            )
        except SyntaxError:
            return
        if violations:
            raise ValueError(f"{context} ('{expression}'): {'; '.join(violations)}")

    def check_state(
        self, key: str, state: State, states: dict[str, State], registry: dict[str, dict[str, str]],
        env_keys: dict[str, EnvKey], sources: dict[str, Source], archives: ProjectArchives,
        namespaces: TriggerNamespaces,
    ) -> None:
        kind = INPUT_PROCESSOR_KINDS[state.input_processor]
        registry = kind.script_registry(registry)
        registry_for_triggers = IdentifierRegistry.for_triggers(registry)
        registry_for_task = IdentifierRegistry.for_task(registry)
        self._cursor.at(state.line, f"states.{key}")
        self.validate_state_sources(state, sources)
        self.validate_state_io(state, env_keys, kind.model_visible_io(state))
        for action in state.actions:
            self._cursor.at(action.line, f"states.{key}.actions.{action.name}")
            action_context = f"State {key}, action '{action.name}'"
            if action.target not in states or action.target == "":
                raise ValueError(
                    f"State '{state.key}', action '{action.name}': "
                    f"target '{action.target}' is not a valid state"
                )
            target_kind = INPUT_PROCESSOR_KINDS.get(
                action.override_target_processor, INPUT_PROCESSOR_KINDS[states[action.target].input_processor],
            )
            registry_for_on_exit = IdentifierRegistry.for_on_exit(target_kind.on_exit_registry(registry))
            namespaces.check_action(state, action, env_keys)
            if action.trigger:
                self.validate_namespaced_expression(
                    action.trigger, f"{action_context}: trigger", registry_for_triggers, sources,
                    namespaces=namespaces.names, known_builtins=TRIGGER_FUNCTION_NAMES,
                )
            if action.task:
                self.validate_task(action.task, action_context, registry_for_task, sources, archives)
            if action.on_exit:
                self.validate_on_exit(
                    action.on_exit, action_context, registry_for_on_exit, sources, env_keys, archives, namespaces.names,
                )

    def validate_state_io(
        self, state: State, env_keys: dict[str, EnvKey], io: tuple[tuple[str, tuple[str, ...]], ...],
    ) -> None:
        for field_name, names in io:
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
                if source.url and method not in self.supported_methods(source):
                    raise ValueError(
                        f"State '{state.key}': {field_name} '{source_name}' references undefined name(s): "
                        f"source.{source_name}.{method}"
                    )

    @staticmethod
    def validate_env_key_type(declared: EnvKey, expression: str, context: str) -> None:
        written_kind = TriggerExpressionAnalyzer.expression_kind(expression)
        if written_kind is None or STORED_ENV_TYPES[declared.type].accepts_kind(written_kind):
            return
        raise ValueError(
            f"{context}: env expression for '{declared.name}' ('{expression}') is a {written_kind}, but "
            f"'{declared.name}' is declared {declared.type} — an env key's type can't change once declared."
        )
