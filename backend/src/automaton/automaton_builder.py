from automaton.automaton import (
    AI_ACCESS_NONE, AI_ACCESS_VALUES, Action, EnvKey, MemoryArchive, Automaton, Reaction, Signal, Source, SourceDict,
    State,
)
from automaton.build_error import AutomatonBuildError
from automaton.identifier_registry import IdentifierRegistry
from automaton.trigger_expression_analyzer import TriggerExpressionAnalyzer
from typing import Any
from logging_factory import LoggerFactory
from metrics.metrics_framework import metric_names
from tracking.actuators import ActuatorSet, MAX_ATTACHMENT_READ_BYTES
from tracking.sources import SOURCE_DRIVERS, READ_METHOD, WRITE_METHOD, driver_class_for
from tracking.sources.avance_env import PATH as AVANCE_ENV_PATH
from tracking.sources.url import parse_source_url

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
import base64
import inspect
from pathlib import Path

logger = LoggerFactory.get_logger(__name__)



def _load_yaml(text: str):
    """A fresh ruamel YAML per call, never a module-level instance: a
    YAML object keeps its reader/scanner/parser/composer as attributes
    of itself for the duration of load(), so one shared instance driven
    from two threads at once (every sync endpoint runs in Starlette's
    threadpool, and the SPA fetches several projects' metadata in
    parallel) interleaves two documents' tokens — ParserError pointing
    at another project's lines, IndexError in the reader, "pop from
    empty list". Constructing one is cheap next to the parse itself."""
    return YAML(typ='rt').load(text)

EXTENSION_TO_MEDIA_TYPE = {
    ".yml": "text/plain",
    ".md": "text/plain",
    ".txt": "text/plain",
    ".csv": "text/plain",
}

VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# A state's three source fields (see automaton.State) and the
# SourceDriver method each one exposes to the model.
STATE_SOURCE_FIELDS = (
    ("ai-may-read-sources", READ_METHOD), ("ai-must-read-sources", READ_METHOD), ("ai-may-write-sources", WRITE_METHOD),
)
# Field names a state used to carry and the one that replaced each — a
# stored or fresh revision declaring one is rejected with a message
# naming the new field instead (no automatic rewrite: see PROJECT_SPECS.md).
LEGACY_STATE_SOURCE_FIELDS = {
    "tools": "ai-may-read-sources",
    "ai-may-query-sources": "ai-may-read-sources",
    "ai-must-query-sources": "ai-must-read-sources",
}

class AutomatonBuilder(object):
    """Builds an Automaton from a project's index.yml: parses the YAML,
    resolves attachments, validates the result, and constructs the
    Automaton — the one place that shape is decided.

    Every raise site below still raises a plain ValueError, exactly as
    before — build() itself is the one place that turns it into an
    AutomatonBuildError, stamped with wherever self._current_line/
    _current_section were last set (see _at/_line_of below) at the
    moment it fired. A fresh AutomatonBuilder is always constructed per
    build() call (never reused/shared across requests — see every call
    site), so this ambient state carries no cross-call concurrency risk."""

    def __init__(self) -> None:
        self._current_line: int | None = None
        self._current_section: str | None = None
        # Non-fatal findings (see _warn) — handed to the Automaton as
        # build_warnings once the build succeeds.
        self._warnings: list[str] = []

    def _warn(self, message: str) -> None:
        logger.warning("Build warning: %s", message)
        self._warnings.append(message)

    def _at(self, line: int | None, section: str) -> None:
        self._current_line = line
        self._current_section = section

    @staticmethod
    def _line_of(parent, key: str) -> int | None:
        """0-based line where `key` is declared inside `parent`, a
        ruamel round-trip CommentedMap — matches CodeEditor.vue's own
        jumpToLine convention. None whenever that's not available (a
        plain dict, or `key` genuinely absent) rather than raising —
        this is best-effort positional context, never load-bearing."""
        try:
            return parent.lc.key(key)[0]
        except (AttributeError, KeyError, TypeError):
            return None

    @staticmethod
    def _own_line(node) -> int | None:
        """0-based line where `node`'s own mapping starts — correct for
        a CommentedMap reached as a *sequence item* (e.g. one action
        inside its state's `actions:` list), where the content starts on
        the same line as the list marker. NOT correct for a CommentedMap
        reached via a parent key (use _line_of instead there) — a child
        reached that way starts its own content one line below the
        parent's own key, so .lc.line would point past it."""
        return getattr(getattr(node, "lc", None), "line", None)

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
    def _find_archive(required_attachment: str, all_archives: dict[str, MemoryArchive], for_field: str) -> str | None:
        """Resolves `required_attachment` to its key in `all_archives` —
        an exact path match first, then a unique basename match. None
        when genuinely absent; still raises when the basename is
        ambiguous, since that's a real authoring error, not an absence."""
        if required_attachment in all_archives:
            return required_attachment
        matches = [archive_name for archive_name in all_archives if Path(archive_name).name == required_attachment]
        if len(matches) > 1:
            raise ValueError(
                f"{for_field} attachment named '{required_attachment}' is ambiguous — "
                f"matches {', '.join(sorted(matches))}"
            )
        return matches[0] if matches else None

    @staticmethod
    def _extract_required_archives(required_attachments: list[str], all_archives: dict[str, MemoryArchive], for_field: str) -> dict[str, MemoryArchive]:
        extracted_archives = {}
        for required_attachment in required_attachments:
            resolved = AutomatonBuilder._find_archive(required_attachment, all_archives, for_field)
            if resolved is None:
                raise ValueError(
                    f"{for_field} attachment named '{required_attachment}' not found"
                )
            extracted_archives[required_attachment] = all_archives[resolved]
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
        *source*, exactly like an action's own `env:` field. `ai-access`
        (default none) says what the model may do with this key through
        an avance:env source; `ai-definition` — the text the model reads
        about it — is required as soon as ai-access isn't none, the same
        requirement a source exposed to the model gets (see
        _actions_sanity_check), optional otherwise."""
        raw_env_key = raw_env_key or {}
        raw_value = raw_env_key.get("value", "")
        value = raw_value if isinstance(raw_value, str) else str(raw_value)
        raw_description = raw_env_key.get("ui-description")
        ai_access = raw_env_key.get("ai-access", AI_ACCESS_NONE)
        if ai_access not in AI_ACCESS_VALUES:
            raise ValueError(
                f"env key '{name}': 'ai-access' must be one of {', '.join(AI_ACCESS_VALUES)}, got {ai_access!r}."
            )
        raw_ai_definition = raw_env_key.get("ai-definition")
        ai_definition = raw_ai_definition.strip() if isinstance(raw_ai_definition, str) and raw_ai_definition.strip() else None
        if ai_access != AI_ACCESS_NONE and ai_definition is None:
            raise ValueError(
                f"env key '{name}': 'ai-access: {ai_access}' requires an 'ai-definition' — the text the model "
                "reads to know what this variable means, the same requirement a source exposed to the model gets."
            )
        return EnvKey(
            name=name,
            value=value.strip(),
            ui_description=raw_description.strip() if raw_description else None,
            ai_access=ai_access,
            ai_definition=ai_definition,
        )

    def _build_source(self, name: str, raw_source: dict, all_archives: dict[str, MemoryArchive]) -> Source:
        """One `sources:` declaration. A freshly-added source (see
        AutomatonYamlEditor.add_source) has no `url` yet — left as "" here
        rather than rejected, the same "created, not yet configured" state
        an env key's own empty default gets; it just can't be usefully
        referenced by a trigger/env: expression yet (see
        _validate_namespaced_expression). Once set, `url`'s scheme picks
        the driver (SOURCE_DRIVERS); for the 'avance' driver, the path
        must resolve to an already-uploaded archive — the same existence
        check `attachments:` already gets (_extract_required_archives),
        except a path the 'avance' driver has never seen before is
        provisioned here as an empty archive rather than rejected: it's
        the project's own embedded default driver, so a source name that
        outruns its backing file (a hand-edited index.yml, a family
        member copied before its archive) still builds, empty, instead
        of failing the whole project. `avance:env` is the one 'avance'
        path with no archive behind it at all (see
        tracking.sources.avance_env) — never provisioned."""
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
            if scheme == "avance" and path != AVANCE_ENV_PATH and self._find_archive(path, all_archives, f"source '{name}'") is None:
                all_archives[path] = MemoryArchive(
                    filename=path,
                    source={"type": "text", "media_type": "text/plain", "data": ""},
                )
        raw_ai_definition = raw_source.get("ai-definition")
        return Source(
            name=name,
            url=url,
            ui_label=raw_source.get("ui-label", name),
            ui_description=raw_source.get("ui-description"),
            ai_definition=raw_ai_definition.strip() if raw_ai_definition else None,
        )

    def _validate_env_sources(self, sources: dict[str, Source], env_keys: dict[str, EnvKey], raw_sources) -> None:
        """An `avance:env` source exposes the env keys with ai-access
        other than none — declaring one when no key exports anything is
        an empty table, always a mistake. The converse (exported keys but
        no avance:env source) is fine: ai-access is harmless until a
        source actually exposes the key, and the key still serves scripts."""
        if any(env_key.exported for env_key in env_keys.values()):
            return
        for name, source in sources.items():
            if source.is_env_source:
                self._at(self._line_of(raw_sources, name), f"sources.{name}")
                raise ValueError(
                    f"Source '{name}': url 'avance:env' exposes the project's env keys to the model, but no env "
                    "key declares 'ai-access: readonly' or 'ai-access: readwrite' — nothing to expose."
                )

    def _validate_env_key_default_order(self, env_keys: dict[str, EnvKey], raw_env_keys) -> None:
        """A later env key's own default may reference an earlier one
        (`env.<name>`); referencing itself or a key declared further down
        is rejected here. Only checked against *known* key names — a
        reference to a name that isn't declared anywhere is left to the
        normal env-expression validation this project's init-action
        already goes through (see build()'s own registry-based pass
        below), which reports that case as its own, clearer "undefined
        name" error rather than a confusing ordering one. Likewise a bad
        expression's own syntax error is left to that same pass — a
        SyntaxError here just means nothing resolves to the 'env'
        namespace, so there's nothing for this check to flag. `raw_env_keys`
        is only for positional context (see _line_of) — never read otherwise."""
        all_names = set(env_keys.keys())
        declared_so_far: set[str] = set()
        for name, env_key in env_keys.items():
            self._at(self._line_of(raw_env_keys, name), f"env.{name}")
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

    def _build_action(self, key: str, raw_action: dict, all_archives: dict[str, MemoryArchive]) -> Action:
        on_enter = raw_action.get("on-enter")
        line = self._own_line(raw_action)
        self._at(line, f"states.{key}.actions.{raw_action.get('name', '?')}")
        return Action(
            name=raw_action["name"],
            ui_description=raw_action.get("ui-description"),
            ui_label=raw_action.get("ui-label") or raw_action["name"],
            ui_button=raw_action.get("ui-button") or raw_action.get("ui-label") or raw_action["name"],
            # Missing 'target' means a self-loop: the action stays
            # on the state it fired from.
            target=raw_action.get("target", key),
            trigger=raw_action.get("trigger"),
            attachments=self._extract_required_archives(raw_action.get("attachments", []), all_archives, f"action {raw_action['name']}"),
            on_enter=on_enter,
            env=self._build_action_env(raw_action.get("env"), raw_action["name"]),
            line=line,
        )

    def _build_state(self, key: str, raw_state: dict, all_archives: dict[str, MemoryArchive], line: int | None = None) -> State:
        self._at(line, f"states.{key}")
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
        self._at(line, f"states.{key}")  # actions loop above moved this on — reset for the state-level checks below
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

        for legacy_field, replacement in LEGACY_STATE_SOURCE_FIELDS.items():
            if legacy_field in raw_state:
                raise ValueError(
                    f"State '{key}': '{legacy_field}' is no longer a valid field — use '{replacement}' instead "
                    "('ai-may-read-sources': the model decides whether to call a source's select; "
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
            # Existence of each name (against this project's own
            # `sources:`), its own required `ai-definition`, and — for a
            # write — its driver's own update support are checked later,
            # once `sources` itself is fully built — see _actions_sanity_check.
            ai_may_read_sources=tuple(raw_source_lists["ai-may-read-sources"]),
            ai_must_read_sources=tuple(raw_source_lists["ai-must-read-sources"]),
            ai_may_write_sources=tuple(raw_source_lists["ai-may-write-sources"]),
            line=line,
        )

    @staticmethod
    def _validate_namespaced_expression(
        expression: str, context: str, registry: dict[str, dict[str, str]], sources: dict[str, Source],
        known_locals: frozenset[str] = frozenset(),
    ) -> None:
        """Syntax + per-namespace identifier validation shared by
        `trigger:`, an action's `env:` expressions, and an on-enter
        statement. Any bare identifier left over must be a core metric —
        or, for on-enter only, a local variable an earlier `name = ...`
        statement in the same script already declared (`known_locals`;
        always empty for trigger:/env:, which have no such thing).
        `source.<name>.<method>` is checked against `sources` directly
        (see TriggerExpressionAnalyzer.source_refs), not `registry` —
        it's a dynamic, per-project namespace the same way
        `automaton.<project>.*` is (see _validate_automaton_refs_exist)."""
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
        for source_name, methods in source_refs.items():
            source = sources.get(source_name)
            if source is None:
                unknown.add(f"source.{source_name}")
                continue
            unknown |= {f"source.{source_name}.{m}" for m in methods - AutomatonBuilder._supported_methods(source)}
        if unknown:
            raise ValueError(f"{context} references undefined name(s): {', '.join(sorted(unknown))}")

    @staticmethod
    def _supported_methods(source: Source) -> frozenset[str]:
        """What `source`'s own driver implements — empty for a source
        with no url yet (see _build_source), which supports nothing: every
        method reference on it is reported unknown, same as one naming an
        unsupported method on a configured source."""
        try:
            return driver_class_for(source.url).SUPPORTED_METHODS
        except (ValueError, KeyError):
            return frozenset()

    @staticmethod
    def _validate_actuator_arity(expression: str, context: str) -> None:
        for method_name, arg_count in TriggerExpressionAnalyzer.namespace_calls(expression, "actuator"):
            method = getattr(ActuatorSet, method_name, None)
            if method is None:
                continue
            expected = len(inspect.signature(method).parameters) - 1
            if arg_count != expected:
                raise ValueError(
                    f"{context} ('{expression}'): actuator.{method_name}(...) takes {expected} "
                    f"argument(s), got {arg_count}"
                )

    @classmethod
    def _validate_attachment_read(cls, expression: str, context: str, all_archives: dict[str, MemoryArchive]) -> None:
        """Every `attachment.read(name)` call in `expression`: `name` must
        be a string literal (see TriggerExpressionAnalyzer.
        attachment_read_violations) resolving — exact path or unique
        basename under `behaviour/`, see _extract_required_archives — to a
        text archive no bigger than MAX_ATTACHMENT_READ_BYTES. All checked
        here, once, so a bad call fails the build instead of a real
        on-enter run."""
        violations = TriggerExpressionAnalyzer.attachment_read_violations(expression)
        if violations:
            raise ValueError(f"{context} ('{expression}'): {'; '.join(violations)}")
        for name in TriggerExpressionAnalyzer.attachment_read_names(expression):
            archive = cls._extract_required_archives([name], all_archives, context)[name]
            if archive.source["type"] != "text":
                raise ValueError(
                    f"{context} ('{expression}'): attachment.read('{name}') targets a binary file — "
                    "only text files can be read this way."
                )
            size = len(archive.source["data"].encode("utf-8"))
            if size > MAX_ATTACHMENT_READ_BYTES:
                raise ValueError(
                    f"{context} ('{expression}'): attachment.read('{name}') is {size} bytes, over the "
                    f"{MAX_ATTACHMENT_READ_BYTES}-byte limit."
                )

    @classmethod
    def _validate_on_enter(
        cls, on_enter: str | None, context: str, registry: dict[str, dict[str, str]], sources: dict[str, Source],
        all_archives: dict[str, MemoryArchive],
    ) -> None:
        """`on-enter`: zero or more `actuator.<name>(...)`/`attachment.read(...)`
        calls, one per top-level statement — e.g. `actuator.celebrate()`
        on its own line, `actuator.notify(user.name, "Hi!")` on another, a
        single call free to span several lines of its own — split via
        TriggerExpressionAnalyzer.on_enter_statements (real Python
        parsing, so blank lines and '#' comments need no special-casing
        here), each validated the same way a trigger is, against the
        actuator view of the registry (see IdentifierRegistry.
        for_actuators): no `session.*`, since an actuator.defer'd call
        outlives the session that fired it. A statement may instead be a
        simple `name = <expr>` assignment (see
        TriggerExpressionAnalyzer.on_enter_assignment) — `<expr>` is
        validated exactly like any other on-enter statement, `name`
        becomes usable, bare, by every later statement in this same
        on-enter script (never earlier ones, never a different action's),
        and must not shadow a reserved namespace or core metric name."""
        if not on_enter:
            return
        try:
            statements = TriggerExpressionAnalyzer.on_enter_statements(on_enter)
        except SyntaxError as exc:
            raise ValueError(f"{context} ('{on_enter}') is not valid on-enter source: {exc}") from exc
        known_locals: set[str] = set()
        for line_number, statement in statements:
            line_context = f"{context}, on-enter line {line_number}"
            assignment = TriggerExpressionAnalyzer.on_enter_assignment(statement)
            target, expression = assignment if assignment is not None else (None, statement)
            if target is not None and (target in TriggerExpressionAnalyzer.RESERVED_NAMESPACES or target in metric_names()):
                raise ValueError(
                    f"{line_context} ('{statement}'): '{target}' is a reserved name "
                    "(a namespace or core metric) and can't be used as an on-enter local variable."
                )
            cls._validate_namespaced_expression(expression, line_context, registry, sources, frozenset(known_locals))
            cls._validate_actuator_arity(expression, line_context)
            cls._validate_attachment_read(expression, line_context, all_archives)
            violations = TriggerExpressionAnalyzer.defer_violations(expression)
            if violations:
                raise ValueError(f"{line_context} ('{statement}'): {'; '.join(violations)}")
            if target is not None:
                known_locals.add(target)

    @staticmethod
    def _validate_trigger_types(expression: str, context: str) -> None:
        """Only for `trigger:`, never `env:` — `env:` allows any simple
        value, not a boolean condition, so it has no comparison shape to
        type-check. Catches comparisons between statically-incompatible types."""
        violations = TriggerExpressionAnalyzer.type_violations(expression)
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
        for project_id, env_keys in TriggerExpressionAnalyzer.automaton_env_refs(expression).items():
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
        env_keys: dict[str, EnvKey], sources: dict[str, Source], all_archives: dict[str, MemoryArchive],
        known_projects: dict[str, frozenset[str]] | None = None,
    ):
        """`registry`: every valid identifier for this project, one set
        per namespace. `env_keys`: for checking an action's own `env:`
        writes against each key's own declared type (see
        _validate_env_key_type below). `sources`: this project's own
        declared `sources:`, keyed by name — see _validate_namespaced_expression.
        `all_archives`: for checking an on-enter's own attachment.read(...)
        calls (see _validate_attachment_read). `known_projects`: None skips
        the automaton.* existence check entirely."""
        registry_without_actuator = IdentifierRegistry.for_triggers(registry)
        registry_without_session = IdentifierRegistry.for_actuators(registry)
        self._at(state.line, f"states.{key}")
        self._validate_state_sources(state, sources, env_keys)
        for action in state.actions:
            self._at(action.line, f"states.{key}.actions.{action.name}")
            if action.target not in declared_states:
                raise ValueError(
                    f"State '{state.key}', action '{action.name}': "
                    f"target '{action.target}' is not a valid state"
                )
            if action.trigger:
                self._validate_namespaced_expression(
                    action.trigger, f"State {key}, action '{action.name}': trigger", registry_without_actuator, sources,
                )
                self._validate_trigger_types(
                    action.trigger, f"State {key}, action '{action.name}': trigger",
                )
                referenced_projects = TriggerExpressionAnalyzer.automaton_project_refs(action.trigger)
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
                        expression, f"State {key}, action '{action.name}': env expression for '{env_key}'",
                        registry_without_actuator, sources,
                    )
                    self._validate_env_key_type(
                        env_keys[env_key], expression, f"State {key}, action '{action.name}'",
                    )
            if action.on_enter:
                self._validate_on_enter(
                    action.on_enter, f"State {key}, action '{action.name}'", registry_without_session, sources,
                    all_archives,
                )

    def _validate_state_sources(self, state: State, sources: dict[str, Source], env_keys: dict[str, EnvKey]) -> None:
        """Every name in a state's three source fields must be a declared
        source with its own `ai-definition`, and its driver must implement
        the method the field exposes (`select` for a read, `update` for a
        write) — reported with the very same "undefined name(s)" wording a
        script calling an unsupported method gets (see
        _validate_namespaced_expression). A source with no url yet (see
        _build_source) keeps its "created, not yet configured" leniency
        for a read; a write on it is still rejected, since no driver
        means no update. An avance:env source the model may write but
        never read in the same state builds (writing blind is legal) but
        is almost certainly an oversight — a warning, not an error. An
        avance:env source in ai-may-write-sources with no `readwrite` key
        at all would give the model an `update` tool whose `fields` schema
        can never have a single property (see AvanceEnvSource.
        parameter_schema) — always a mistake, so it's a build error here
        rather than a tool the model can call but never usefully use."""
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
                if (source.url or method == WRITE_METHOD) and method not in self._supported_methods(source):
                    raise ValueError(
                        f"State '{state.key}': {field_name} '{source_name}' references undefined name(s): "
                        f"source.{source_name}.{method}"
                    )
        any_readwrite_key = any(env_key.writable for env_key in env_keys.values())
        for source_name in state.ai_may_write_sources:
            source = sources[source_name]
            if source.is_env_source and source_name not in state.ai_read_source_names:
                self._warn(
                    f"State '{state.key}': ai-may-write-sources '{source_name}' — the model may write the env "
                    f"here but never sees the current values: add '{source_name}' to 'ai-may-read-sources' or "
                    "'ai-must-read-sources' too."
                )
            if source.is_env_source and not any_readwrite_key:
                raise ValueError(
                    f"State '{state.key}': ai-may-write-sources '{source_name}' — url 'avance:env' exposes the "
                    "model an 'update' tool, but no env key declares 'ai-access: readwrite' — nothing it could "
                    "ever write."
                )

    @staticmethod
    def _validate_env_key_type(declared: EnvKey, expression: str, context: str) -> None:
        """An env key's own declared `value` (its default) fixes that
        key's type for good, the same way a signal's definition always
        yields a number — a later write can update *what* the key holds,
        never *what kind of thing* it holds. Silently skipped (never a
        false positive) whenever either side's kind isn't statically
        knowable: `declared.value` is empty (no default was ever given,
        so the key was never typed to begin with), or either expression
        combines values in a way TriggerExpressionAnalyzer.expression_kind
        can't see through (e.g. `env.other_key`, arithmetic, a bare name)."""
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

    def _build_init_action(self, raw: dict, env_keys: dict[str, EnvKey]) -> Action:
        """`env_keys`: every project-level `env:` declaration's own
        default is folded into this synthetic action's own `env:` field
        first, so its one-time firing at session bootstrap (see
        ChatService.open_if_needed) evaluates each through the same
        eval_action_env path a real action's `env:` uses. init-action can
        also declare its own explicit `env:` mapping, exactly like a
        regular action — applied on top, so it overrides a given key's
        declared default rather than replacing every key's default outright."""
        line = self._line_of(raw, "init-action")
        self._at(line, "init-action")
        raw_init_action = raw.get("init-action")
        if not isinstance(raw_init_action, dict) or not raw_init_action.get("target"):
            raise ValueError(
                "'init-action' is required and must be a mapping with at least a 'target' "
                "field — the project's real starting state."
            )
        init_on_enter = raw_init_action.get("on-enter")
        env = {name: env_key.value for name, env_key in env_keys.items() if env_key.value}
        env.update(self._build_action_env(raw_init_action.get("env"), "init-action") or {})
        init_action = Action(
            name="init-action",
            ui_description=raw_init_action.get("ui-description"),
            ui_label=raw_init_action.get("ui-label", "init-action"),
            ui_button="",
            target=raw_init_action["target"],
            on_enter=init_on_enter,
            env=env or None,
            line=line,
        )
        return init_action


    @staticmethod
    def _build_project_metadata(
        raw: dict, *, legacy_project_id: str | None = None,
    ) -> tuple[str, str | None, int, str | None, str | None, bool, bool]:
        """The `project:` section — id/family/revision/ui-label/
        ui-description/signal-tracking-on-ai-message/talk-enabled. `id`
        is mandatory: a plain identifier (letters, digits, underscores,
        not starting with a digit — the same grammar Python's own
        attribute access requires, since `automaton.<id>.*` is exactly
        that), this project's sole identity everywhere; global uniqueness
        is ProjectService's concern. `family` is optional, free-form text
        (never parsed — e.g. a reverse-DNS style "com.example.app" is
        fine), never displayed, and never itself referenced anywhere: it
        only ever gates automaton.* visibility (see AutomatonLoader.
        known_projects_env_keys) — two projects can observe/notify each
        other only when both declare the exact same family. Absent
        (None) means this project can neither observe anything nor be
        observed by anything, itself included. `revision`
        defaults to 0 for a project that never declared one (e.g. a fresh
        import) — automatically overwritten on every publish (see
        ProjectManager.publish_project).

        `legacy_project_id`: only AutomatonLoader passes this, for a
        revision already stored in the Archive table — a stored revision
        predating the `project.id` requirement (every session pinned to
        it via ChatSession.project_revision must stay loadable forever)
        has no `project:` section at all, or one without `id`, and its
        identity is the Archive row's own project_id, which the loader
        knows. An upload/edit never passes it: a new index.yml without
        project.id is still rejected exactly as before."""
        raw_project = raw.get("project")
        if raw_project is None and legacy_project_id is not None:
            raw_project = {"id": legacy_project_id}
        elif isinstance(raw_project, dict) and "id" not in raw_project and legacy_project_id is not None:
            raw_project = {**raw_project, "id": legacy_project_id}
        if not isinstance(raw_project, dict):
            raise ValueError(
                "'project' is required and must be a mapping of fields (id, family, ui-label, "
                "ui-description, signal-tracking-on-ai-message, talk-enabled), got "
                f"{type(raw_project).__name__ if raw_project is not None else 'nothing'}."
            )
        project_id = raw_project.get("id")
        if not isinstance(project_id, str) or not project_id.isidentifier():
            raise ValueError(
                f"project.id {project_id!r} is required and must be a valid identifier — letters, "
                "digits, and underscores only, and it can't start with a digit."
            )
        family = raw_project.get("family") or None
        if family is not None and not isinstance(family, str):
            raise ValueError(f"project.family {family!r} must be a string.")
        revision = raw_project.get("revision", 0)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ValueError(f"project.revision {revision!r} must be a non-negative integer.")
        return (
            project_id, family, revision, raw_project.get("ui-label"), raw_project.get("ui-description"),
            raw_project.get("signal-tracking-on-ai-message", False),
            raw_project.get("talk-enabled", True),
        )

    @staticmethod
    def read_declared_env_keys(index_yml_text: str) -> tuple[str | None, str | None, frozenset[str]]:
        """Reads only `project.id`/`project.family` and the top-level
        `env:` key names, never a full build — lets ProjectService
        validate automaton.* references without building every other
        project. Malformed id/env reports nothing."""
        try:
            raw = _load_yaml(index_yml_text)
        except Exception as exc:
            logger.warning("Failed to parse index.yml for known_projects_env_keys: %s", exc)
            return None, None, frozenset()
        if not isinstance(raw, dict):
            return None, None, frozenset()
        raw_project = raw.get("project")
        project_id = raw_project.get("id") if isinstance(raw_project, dict) else None
        if not isinstance(project_id, str) or not project_id.isidentifier():
            project_id = None
        family = raw_project.get("family") if isinstance(raw_project, dict) else None
        if not isinstance(family, str) or not family:
            family = None
        raw_env = raw.get("env")
        env_keys = frozenset(raw_env.keys()) if isinstance(raw_env, dict) else frozenset()
        return project_id, family, env_keys

    @staticmethod
    def peek_declared_revision(index_yml_text: str) -> int | None:
        """Reads only `project.revision`, never a full build — None both
        for a malformed/unparseable file and for one that simply never
        declared a revision (ProjectManager.put_project auto-numbers that
        case as a back-compat import); an invalid-but-present value
        surfaces properly through the real build() call right after."""
        try:
            raw = _load_yaml(index_yml_text)
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None
        raw_project = raw.get("project")
        if not isinstance(raw_project, dict):
            return None
        revision = raw_project.get("revision")
        return revision if isinstance(revision, int) and not isinstance(revision, bool) else None

    def build(
        self, contents: dict, known_projects: dict[str, frozenset[str]] | None = None, *,
        legacy_project_id: str | None = None,
    ) -> Automaton:
        """`known_projects`: every *other* project's own project.id
        mapped to its declared env key names, for validating an
        automaton.<id>.env.<key> reference — already narrowed to this
        project's own family (see AutomatonLoader.known_projects_env_keys),
        so a cross-family (or family-less) id simply isn't here. None
        skips that check. `legacy_project_id`: see _build_project_metadata.

        Every ValueError raised below (directly, or by anything this
        calls) is caught once, here, and turned into an AutomatonBuildError
        carrying whatever self._current_line/_current_section were last
        set to (see _at/_line_of) — never re-derived at each individual
        raise site. An AutomatonBuildError raised directly (there are
        none today, but a future call site might) passes through unchanged.
        A YAMLError (malformed index.yml — ruamel raises its own
        exception hierarchy, not ValueError, straight out of _load_yaml)
        is wrapped the same way: it's just as much a build failure as any
        validation error below it, and every caller (AutomatonLoader,
        ProjectHealthChecker) only ever prepares to catch ValueError."""
        try:
            return self._build(contents, known_projects, legacy_project_id=legacy_project_id)
        except AutomatonBuildError:
            raise
        except (ValueError, YAMLError) as exc:
            raise AutomatonBuildError(
                str(exc), line=self._current_line, section=self._current_section,
            ) from exc

    def _build(
        self, contents: dict, known_projects: dict[str, frozenset[str]] | None, *,
        legacy_project_id: str | None,
    ) -> Automaton:
        all_archives = self._convert_contents_to_archives(contents=contents)

        raw = _load_yaml(contents['index.yml'])
        if not isinstance(raw, dict):
            raise ValueError(f"index.yml must be a YAML mapping at the top level, got {type(raw).__name__}.")

        self._at(self._line_of(raw, "project"), "project")
        project_id, project_family, project_revision, project_ui_label, project_ui_description, autotracking_on_ai_message, talk_enabled = (
            self._build_project_metadata(raw, legacy_project_id=legacy_project_id)
        )

        raw_signals = raw.get("signals", {})
        if not isinstance(raw_signals, dict):
            self._at(self._line_of(raw, "signals"), "signals")
            raise ValueError(f"'signals' must be a mapping of signal name -> fields, got {type(raw_signals).__name__}.")

        signals: dict[str, Signal] = {}
        signal_names_by_ui_label: dict[str, str] = {}
        for name, raw_signal in raw_signals.items():
            self._at(self._line_of(raw_signals, name), f"signals.{name}")
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
            self._at(None, "signals")
            raise ValueError(
                "Signal name(s) reserved for core metrics (see metrics_framework) cannot be "
                f"reused as signal names: {', '.join(sorted(reserved_names))}"
            )

        raw_reactions = raw.get("reactions", {})
        if not isinstance(raw_reactions, dict):
            self._at(self._line_of(raw, "reactions"), "reactions")
            raise ValueError(f"'reactions' must be a mapping of reaction name -> fields, got {type(raw_reactions).__name__}.")

        reactions: dict[str, Reaction] = {}
        reaction_names_by_ui_label: dict[str, str] = {}
        for name, raw_reaction in raw_reactions.items():
            self._at(self._line_of(raw_reactions, name), f"reactions.{name}")
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
            self._at(self._line_of(raw, "env"), "env")
            raise ValueError(f"'env' must be a mapping of env key -> fields, got {type(raw_env_keys).__name__}.")
        env_keys: dict[str, EnvKey] = {}
        for name, raw_env_key in raw_env_keys.items():
            self._at(self._line_of(raw_env_keys, name), f"env.{name}")
            env_keys[name] = self._build_env_key(name, raw_env_key)

        raw_sources = raw.get("sources", {})
        if not isinstance(raw_sources, dict):
            self._at(self._line_of(raw, "sources"), "sources")
            raise ValueError(f"'sources' must be a mapping of source name -> fields, got {type(raw_sources).__name__}.")
        sources: dict[str, Source] = {}
        for name, raw_source in raw_sources.items():
            self._at(self._line_of(raw_sources, name), f"sources.{name}")
            sources[name] = self._build_source(name, raw_source, all_archives)
        self._validate_env_sources(sources, env_keys, raw_sources)
        # Unlike every other forward reference in this file (see the
        # comment on Pass 1 below), env keys' own defaults are a real
        # exception: they're applied top-to-bottom, once, the first time
        # a project's session opens (ChatService._apply_declared_env_
        # defaults evaluates them one at a time, in this same order, so
        # each one only ever sees an *earlier* key's value already
        # persisted) — so a later key's default may reference an earlier
        # one, the same way a plain sequential variable declaration
        # would, but never the other way around.
        self._validate_env_key_default_order(env_keys, raw_env_keys)

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

        # Pass 1: build every state/action first, no expression validation
        # yet — forward references to a state/signal/env key declared
        # elsewhere in the file are fine, so every state must exist first.
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

            states[key] = self._build_state(key, raw_state, all_archives, line=state_line)
            existing_key = state_keys_by_ui_label.get(states[key].ui_label)
            if existing_key is not None:
                raise ValueError(
                    f"States '{existing_key}' and '{key}' both use ui-label "
                    f"'{states[key].ui_label}' — ui-label must be unique across all states."
                )
            state_keys_by_ui_label[states[key].ui_label] = key

        # Pass 2: build the identifier registry, the single source
        # _actions_sanity_check validates every trigger/env: expression
        # against below. An env key's own `value` (its default) is
        # validated here too, for free — it was folded into init_action's
        # own `env:` field above, so init_action's state ("") passes
        # through the very same action.env expression validation as any
        # other action's.
        registry = IdentifierRegistry.build(list(signals.values()), list(env_keys.values()))
        for key, state in states.items():
            context_key = init_action.name if key == "" else key
            self._actions_sanity_check(
                context_key, state, set(raw_states.keys()), registry, env_keys, sources, all_archives, known_projects,
            )

        general_attachments = self._extract_required_archives(raw.get('attachments', []), all_archives, for_field="global")

        return Automaton(
            init_action=init_action,
            states=states,
            general_prompt=raw.get("general-prompt", ""),
            signals=list(signals.values()),
            reactions=list(reactions.values()),
            env_keys=list(env_keys.values()),
            sources=list(sources.values()),
            general_attachments=general_attachments,
            attachments=all_archives,
            autotracking_on_ai_message=autotracking_on_ai_message,
            project_id=project_id,
            project_family=project_family,
            project_revision=project_revision,
            project_ui_label=project_ui_label,
            project_ui_description=project_ui_description,
            talk_enabled=talk_enabled,
            build_warnings=self._warnings,
        )
