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
    # Passed through to the frontend as-is when this action fires — see
    # Automaton.get_state_payload. Not state-level: two different actions
    # landing on the same target state can each have their own value (or
    # none), since it describes *how you got there*, not the destination
    # itself.
    on_enter: str | None = None
    # {env key: expression source}, evaluated (see Automaton.
    # eval_action_env) whenever this action fires — manually or via
    # auto-tracking's trigger — and merged onto tracking.env.Env's own
    # action_set store (see tracking_engine.py's own apply_action_env)
    # so the *next* prompt already sees the updated value. Each
    # expression shares the exact same variable scope/mechanics as a
    # `trigger` (see _eval_trigger) — the signal/env/system/session
    # namespaces plus any referenced metric (see tracking.evaluation_
    # scope.EvaluationScopeBuilder) — just without the boolean cast,
    # since a result here can be any simple value, not only true/false.
    # Normalized to a string at build time even for a YAML value that
    # isn't naturally one (e.g. `True`, `42`) — see automaton_builder.
    # py's _build_action — so this is always Python-expression source,
    # exactly like `trigger`.
    env: dict[str, str] | None = None

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
    # If true, messages from before the transition into this state are kept
    # out of both the AI reply and auto-tracking's signal evaluation.
    history_cutoff: bool = False
    # If false, chat turns are rejected while this is the current state
    # (see chat.turn_processor.TurnProcessor._begin_turn) — independent of
    # fixed_message/history_cutoff: neither implies this.
    chat: bool = True

    @property
    def has_triggerable_actions(self) -> bool:
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


@dataclass
class EnvKey:
    """One project-level `env:` declaration (parallel to `signals:`) —
    the single source of every `env.<name>` key an action's own `env:`
    field is allowed to write to (see automaton_builder.py's
    _actions_sanity_check) and every one a trigger/env expression is
    allowed to *read* (see automaton.identifier_registry.build_registry).
    `value`: a Python-expression source string, same mechanics/scope as
    an action's own env: expression (see Action.env's own docstring) —
    this key's own default, evaluated once whenever nothing has set it
    yet. Always normalized to a string at build time, exactly like
    Action.env's own values (see automaton_builder.py's _build_env_key)."""
    name: str
    value: str = ""
    ui_description: str | None = None


# Functional syntax (not the class form the other Payload types use):
# "on-enter" isn't a valid Python identifier, so a class body couldn't
# declare it — see Automaton.get_state_payload, the one place this key is
# actually produced, matching the same "on-enter" spelling the YAML
# format itself uses (see automaton_builder.py's _build_action).
ActionPayload = TypedDict("ActionPayload", {
    "name": str,
    "ui_label": str,
    "ui_button": str,
    "ui_description": str | None,
    "target": str,
    "has_trigger": bool,
    "on-enter": str | None,
})

class StatePayload(TypedDict):
    key: str
    ui_label: str
    ui_description: str | None
    final: bool
    chat: bool
    actions: list[ActionPayload]

class SignalPayload(TypedDict):
    name: str
    ui_label: str | None
    ui_description: str | None
    definition: str
    attachments: dict[str, MemoryArchive]
    error: bool | None

class EnvKeyPayload(TypedDict):
    name: str
    ui_description: str | None
    value: str

class ProjectPayload(TypedDict):
    id: str | None
    ui_label: str | None
    ui_description: str | None

# The reserved namespaces a trigger/env expression resolves against (see
# tracking.evaluation_scope.EvaluationScopeBuilder) — every top-level
# `<namespace>.<attr>` access in an expression's own AST is one of these.
# A core metric name (see metrics_framework.metric_names) stays a bare,
# unnamespaced identifier — untouched by this set. `automaton` (see
# trigger_automaton_project_refs below) is listed here too, purely so
# trigger_bare_names excludes its own root identifier from "undefined
# bare name" candidates — it's deliberately never given an entry in
# _NAMESPACE_PATHS/NESTED_NAMESPACES below, since automaton.<project>.
# state/env.<key> is a dynamic, per-project chain (the project name is
# never a fixed literal the way "metric" is for session.metric) that
# _namespace_path_of's own static-tuple matching can't express. That's
# also why _validate_namespaced_expression (automaton_builder.py) never
# flags an automaton.* reference as an unknown identifier either way:
# trigger_namespace_refs finds no entry for it (see _namespace_path_of
# returning None for anything whose namespace path isn't literally in
# _NAMESPACE_PATHS), so there's simply nothing to validate against a
# registry — only automaton_builder.py's own dedicated self-loop-only
# check (see trigger_automaton_project_refs) ever inspects an automaton.*
# reference at all.
RESERVED_NAMESPACES = ("signal", "env", "system", "session", "metric", "automaton")

# Dotted sub-namespaces nested one level under a reserved namespace above
# — today just `session.metric` (see tracking.session_facts.SessionFacts.
# metric). Every entry here is matched as a *whole* path: `session.metric.
# <attr>` is a reference into this sub-namespace, `session.<attr>` (with
# no `.metric` in between) stays a reference into plain `session` — see
# _namespace_path/_maximal_attribute_nodes below, which tell the two
# apart by always matching an expression's longest attribute chain first.
NESTED_NAMESPACES = (("session", "metric"),)

_NAMESPACE_PATHS: tuple[tuple[str, ...], ...] = tuple((ns,) for ns in RESERVED_NAMESPACES) + NESTED_NAMESPACES


def _maximal_attribute_nodes(tree: ast.AST) -> list[ast.Attribute]:
    """Every ast.Attribute node in `tree` that isn't itself nested inside
    a longer attribute chain — e.g. for `session.metric.engagement()`
    this is only the outermost `....engagement` node, never the inner
    `session.metric` one, so a dotted chain is only ever matched against
    its full, longest namespace path (see _namespace_path_of), never
    partially re-matched against a shorter one along the way."""
    nested_value_ids = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    return [node for node in ast.walk(tree) if isinstance(node, ast.Attribute) and id(node) not in nested_value_ids]


def _namespace_path_of(node: ast.Attribute) -> tuple[tuple[str, ...], str] | None:
    """(namespace_path, leaf_attr) for `node` if its full dotted chain,
    root to leaf, is exactly one of _NAMESPACE_PATHS plus one more
    attribute (e.g. `session.metric.engagement` -> (("session",
    "metric"), "engagement"), `signal.mood` -> (("signal",), "mood")) —
    None if it isn't a namespace reference at all (some other/unrelated
    attribute access)."""
    attrs = [node.attr]
    cur = node.value
    while isinstance(cur, ast.Attribute):
        attrs.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    chain = (cur.id, *reversed(attrs))
    path, leaf = chain[:-1], chain[-1]
    return (path, leaf) if path in _NAMESPACE_PATHS else None


def _namespace_attrs(tree: ast.AST, *namespace: str) -> set[str]:
    refs = (_namespace_path_of(node) for node in _maximal_attribute_nodes(tree))
    return {ref[1] for ref in refs if ref is not None and ref[0] == namespace}


def trigger_signal_names(expression: str) -> set[str]:
    """Every `signal.<name>` referenced in a trigger/env expression, e.g.
    "signal.daysSinceLastEvent >= 85" -> {"daysSinceLastEvent"}. Used to
    validate a trigger's own signal references at boot, to scope which
    signals a turn's own signal-computation prompt asks about (see
    tracking.definitions.Signals.get_definition), to report which
    signals actually drove a transition, and to rewrite a trigger when
    the signal it references is renamed/deleted (see automaton_yaml_
    editor.py)."""
    tree = ast.parse(expression, mode="eval")
    return _namespace_attrs(tree, "signal")


def trigger_bare_names(expression: str) -> set[str]:
    """Every identifier referenced *outside* one of the reserved
    namespaces (see RESERVED_NAMESPACES) — what a core metric name, or a
    leftover un-migrated bare signal reference, looks like. A name that's
    actually the root of a *nested* namespace (see NESTED_NAMESPACES,
    e.g. `session` in `session.metric.engagement()`) is still excluded
    here exactly like a plain one — this only ever needs the namespace's
    own root identifier, never how deep the chain built on it goes. Used
    to detect a metric reference (see Automaton.triggers_reference) and,
    at build time, any name that isn't a recognized metric either (see
    automaton_builder.py's own validation)."""
    tree = ast.parse(expression, mode="eval")
    namespace_bases = {
        node.value.id for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in RESERVED_NAMESPACES
    }
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} - namespace_bases


def trigger_automaton_project_refs(expression: str) -> set[str]:
    """Every project name referenced as `automaton.<project>...` in
    `expression` — e.g. "automaton.otherProject.state == 'x'" ->
    {"otherProject"}, "automaton.a.env.k1 and automaton.b.state" ->
    {"a", "b"}. Unlike trigger_signal_names/trigger_namespace_refs (see
    RESERVED_NAMESPACES' own docstring on why `automaton` never gets a
    _NAMESPACE_PATHS entry), this walks *every* Attribute node, not just
    maximal ones — an automaton.<project> reference is meaningful
    wherever it appears in a chain (automaton.<project>.state as much as
    automaton.<project>.env.<key>), so nothing here needs "longest chain
    wins" disambiguation the way a fixed-depth namespace does. Used by
    automaton_builder.py's own build-time self-loop-only check, and by
    project_service.py's own reverse-index build (which project(s) does
    this project's own self-loop actions observe)."""
    tree = ast.parse(expression, mode="eval").body
    return {
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "automaton"
    }


def trigger_automaton_env_refs(expression: str) -> dict[str, set[str]]:
    """Every `automaton.<project>.env.<key>` reference in `expression`,
    grouped by project — e.g. "automaton.a.env.k1 and automaton.b.env.k2"
    -> {"a": {"k1"}, "b": {"k2"}}. A narrower walk than trigger_automaton_
    project_refs above (which matches automaton.<project> at any depth,
    automaton.<project>.state included): this only matches the specific
    4-level automaton.<project>.env.<key> chain, since only that shape
    names an actual env key to check the existence of (see automaton_
    builder.py's own build-time known_projects validation — Prompt 10)."""
    tree = ast.parse(expression, mode="eval").body
    refs: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)):
            continue
        env_node = node.value
        if env_node.attr != "env" or not isinstance(env_node.value, ast.Attribute):
            continue
        project_node = env_node.value
        if not isinstance(project_node.value, ast.Name) or project_node.value.id != "automaton":
            continue
        refs.setdefault(project_node.attr, set()).add(node.attr)
    return refs


def trigger_namespace_refs(expression: str) -> dict[str, set[str]]:
    """{'signal': {...}, 'env': {...}, 'system': {...}, 'session': {...},
    'session.metric': {...}, 'metric': {...}} — every namespace attribute
    reference in `expression` (see _NAMESPACE_PATHS — every reserved
    namespace plus any nested one), one entry per namespace actually
    used (a namespace nothing references is simply absent, never an
    empty set), keyed by its dotted path (e.g. "session.metric"). Used
    only by automaton_builder.py's own build-time validation — nothing
    at runtime needs this broken out by namespace, see
    trigger_signal_names/trigger_bare_names above for the two that do."""
    tree = ast.parse(expression, mode="eval")
    refs: dict[str, set[str]] = {}
    for node in _maximal_attribute_nodes(tree):
        ref = _namespace_path_of(node)
        if ref is None:
            continue
        path, leaf = ref
        refs.setdefault(".".join(path), set()).add(leaf)
    return refs


# Every identifier whose runtime *type* (not value) is fixed by its own
# contract, well enough to be checked statically — signal.*/session.
# metric.*/metric.* are always numbers (see tracking.evaluator.
# SignalEvaluator._validate_one's own docstring: not range-checked, but
# genuinely never anything but int/float/None), a specific system.*/
# session.* one has its own fixed type (see tracking.system_facts.
# SystemFacts/tracking.session_facts.SessionFacts). `env.*` is
# deliberately absent: it's a free-form store an action's own `env:`
# expression can set to anything (see Action.env's own docstring, "any
# simple value") — its type is never knowable ahead of a real turn, so
# trigger_type_violations below always treats it (and anything else this
# table has no entry for) as unknown rather than guessing.
_KIND_NUMBER = "number"
_KIND_STRING = "string"
_KIND_BOOL = "bool"
# A kind counts as "number-like" for ordering purposes: Python itself
# treats bool as an int subtype (`True >= 0.5` is legal), so mixing the
# two is never actually a runtime error.
_NUMERIC_KINDS = (_KIND_NUMBER, _KIND_BOOL)

_FIXED_IDENTIFIER_KIND: dict[tuple[str, ...], dict[str, str]] = {
    ("system",): {"today": _KIND_STRING, "time": _KIND_STRING},
    ("session",): {
        "current_session_duration_in_minutes": _KIND_NUMBER,
        "last_user_session_datetime": _KIND_STRING,
        "number_of_user_sessions": _KIND_NUMBER,
        "state_duration_in_minutes": _KIND_NUMBER,
    },
}
# Every identifier under these namespaces is a number, with no per-name
# exceptions to look up (unlike system/session above) — signals by
# contract, metrics because BaseMetric.result always clamps into
# [0, 100] as a float (see metrics_framework.metrics.base).
_ALWAYS_NUMERIC_NAMESPACES = (("signal",), ("session", "metric"), ("metric",))

_ORDERING_OPS: dict[type, str] = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}


def _leaf_kind(node: ast.AST) -> str | None:
    """`node`'s own statically-known kind ('number'/'string'/'bool'), if
    it's a leaf whose type is actually knowable ahead of a real turn —
    None (unknown, never "wrong") for anything else: a bare name, an
    `env.*` reference, an arithmetic/boolean sub-expression, ... —
    trigger_type_violations only ever flags a comparison where *both*
    sides are actually known and incompatible, never one built on a
    guess."""
    if isinstance(node, ast.Call):
        return _leaf_kind(node.func)
    if isinstance(node, ast.Attribute):
        ref = _namespace_path_of(node)
        if ref is None:
            return None
        path, leaf = ref
        fixed = _FIXED_IDENTIFIER_KIND.get(path, {}).get(leaf)
        if fixed is not None:
            return fixed
        return _KIND_NUMBER if path in _ALWAYS_NUMERIC_NAMESPACES else None
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return _KIND_BOOL
        if isinstance(node.value, (int, float)):
            return _KIND_NUMBER
        if isinstance(node.value, str):
            return _KIND_STRING
        return None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _leaf_kind(node.operand)
        return inner if inner in _NUMERIC_KINDS else None
    return None


def trigger_type_violations(expression: str) -> list[str]:
    """Every ordering comparison (`<`/`<=`/`>`/`>=` — never `==`/`!=`,
    which Python allows between any two types unconditionally, always
    just returning False for a mismatch rather than raising) in
    `expression` between two operands whose statically-known kinds (see
    _leaf_kind) are actually incompatible — e.g. `system.today() >= 5`
    (a date string compared against a number), which would raise
    TypeError the moment this trigger is ever evaluated. A pair where
    either side's kind isn't statically knowable (an `env.*` reference,
    a bare name, a nested arithmetic expression, ...) has nothing this
    can check, so it's silently skipped rather than guessed at — this
    only ever flags a comparison *proven* to fail, never a merely
    suspicious one. Returns human-readable messages, never raises
    itself: the caller decides what to do with them (see
    AutomatonBuilder._actions_sanity_check)."""
    tree = ast.parse(expression, mode="eval").body
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        for left, op, right in zip(operands, node.ops, operands[1:]):
            symbol = _ORDERING_OPS.get(type(op))
            if symbol is None:
                continue
            left_kind, right_kind = _leaf_kind(left), _leaf_kind(right)
            if left_kind is None or right_kind is None:
                continue
            if left_kind == right_kind or (left_kind in _NUMERIC_KINDS and right_kind in _NUMERIC_KINDS):
                continue
            violations.append(
                f"'{ast.unparse(left)} {symbol} {ast.unparse(right)}' compares a {left_kind} "
                f"with a {right_kind} — this will raise a TypeError as soon as it's evaluated"
            )
    return violations


class Automaton(object):

    def __init__(
        self,
        init_action: Action,
        states: dict[str, State],
        general_prompt: str,
        signals: list[Signal],
        attachments: dict[str, MemoryArchive],
        general_attachments: dict[str, MemoryArchive],
        autotracking_on_ai_message: bool,
        # Defaults to [] rather than being required: every direct
        # (non-AutomatonBuilder) construction site across the test suite
        # predates this field and has no declared env: keys of its own to
        # report — only AutomatonBuilder.build (the one place a project's
        # own declared env: section is ever actually parsed) passes a
        # real list.
        env_keys: list[EnvKey] | None = None,
        # The optional top-level `project:` section (id/ui-label/
        # ui-description — see automaton_builder.py's own
        # _build_project_metadata) — `project_id` is the identifier this
        # project is reachable as from *other* projects' own automaton.*
        # references (see trigger_automaton_project_refs), globally
        # unique across every project (checked by ProjectService, which
        # has the database this class never touches — see its own
        # _validate_project_id_globally_unique). All three default to
        # None for the same reason env_keys defaults to [] above: every
        # direct-construction call site across the test suite predates
        # this field.
        project_id: str | None = None,
        project_ui_label: str | None = None,
        project_ui_description: str | None = None,
    ):
        # Replaces the old bare `initial_state: str` field: same
        # information (its .target), but as a real action so it can carry
        # an action_prompt too — see ChatService.open_if_needed, the one
        # place that ever executes it.
        self.init_action = init_action
        self.states = states
        self.general_prompt = general_prompt
        self.signals = signals
        self.env_keys = env_keys or []
        self.project_id = project_id
        self.project_ui_label = project_ui_label
        self.project_ui_description = project_ui_description
        self.general_attachments = general_attachments
        self.attachments = attachments
        # The two auto-tracking modes (before/after the AI reply) are
        # mutually exclusive — a single flag selects between them, see
        # tracking/tracking_service.py's TrackingService.process() and
        # tracking/tracking_processor.py's build_turn_protocol().
        self.autotracking_on_ai_message = autotracking_on_ai_message

    def get_state(self, state_key: str) -> State:
        return self.states[state_key]

    @staticmethod
    def get_action_payload(action: Action) -> ActionPayload:
        """Serializes `action` into the plain-dict shape every
        action-reporting endpoint sends to the frontend — the one place
        this shape is built, so it can't drift between call sites (see
        get_state_payload's own nested use, and project_service.py's
        get_project_graph's own flat edge list). Deliberately never
        includes `trigger`'s own raw expression — just whether one is
        set (the frontend uses this to decide button visibility, see
        ActionButtons.vue, never to evaluate anything itself) — the
        expression itself (thresholds, risk-detection conditions) is
        internal transition logic that must never reach a live chat
        client, only ever the "Edit project" view's own Inspect panel
        (see get_project_graph's own edge wrapper, which carries it
        alongside this payload instead of inside it)."""
        return {
            "name": action.name,
            "ui_label": action.ui_label,
            "ui_button": action.ui_button,
            "ui_description": action.ui_description,
            "target": action.target,
            "has_trigger": action.trigger is not None,
            "on-enter": action.on_enter,
        }

    @staticmethod
    def get_signal_payload(signal: Signal) -> SignalPayload:
        """Serializes `signal` into the plain-dict shape every
        signal-reporting endpoint sends to the frontend. `attachments`
        stays empty here deliberately: the real dict[str, MemoryArchive]
        (full file content, base64-encoded for a binary one) would make
        every caller of this — including project_service.py's own
        get_project_signals, refreshed on every Inspector Signals-tab
        open — pay to ship whole file bodies just to know their own
        names; a caller that needs the names themselves (that one does)
        reads them off the same Signal object directly instead."""
        return {
            "name": signal.name,
            "ui_label": signal.ui_label,
            "ui_description": signal.ui_description,
            "definition": signal.definition,
            "attachments": {},
            "error": None,
        }

    @staticmethod
    def get_env_key_payload(env_key: EnvKey) -> EnvKeyPayload:
        """Serializes `env_key` into the plain-dict shape every
        env-key-reporting endpoint sends to the frontend — mirrors
        get_signal_payload's own role for Signal."""
        return {
            "name": env_key.name,
            "ui_description": env_key.ui_description,
            "value": env_key.value,
        }

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
            "chat": state.chat,
            "actions": [Automaton.get_action_payload(a) for a in state.actions],
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
        least one of `names`, as a *bare* (unnamespaced) identifier, in
        its trigger expression — in practice always a core metric name
        (see metrics_framework.metric_names, the only bare-identifier
        caller left — signal/env/system/session are namespaced now, see
        RESERVED_NAMESPACES, and no longer need this same skip-if-
        unreferenced check: see EvaluationScopeBuilder's own docstring
        for why). Lets a caller skip resolving an expensive extra value
        set (see MetricService.merge_if_referenced) before evaluation,
        whenever nothing in this state's triggers could possibly use it."""
        state = self.states[state_key]
        return any(
            action.trigger and trigger_bare_names(action.trigger) & names for action in state.actions
        )

    def triggerable_signal_names(self, state_key: str) -> set[str]:
        state = self.states[state_key]
        referenced: set[str] = set()
        for action in state.actions:
            if action.trigger:
                referenced |= trigger_signal_names(action.trigger)
            if action.env:
                for expression in action.env.values():
                    referenced |= trigger_signal_names(expression)
        return referenced & {s.name for s in self.signals}

    def all_triggerable_signal_names(self) -> set[str]:
        """triggerable_signal_names, unioned across every state — not
        scoped to wherever the conversation currently happens to be. The
        project-wide "is this signal used by anything at all" view (see
        project_service.py's get_project_signals, whose own `relevant`
        field this backs — the Inspector Signals tab's "show only
        relevant signals" filter): a signal only some *other* state's
        actions reference is still meaningfully "relevant" from that
        vantage point, unlike the single-state scope autotracking itself
        needs for its own prompt/computation scoping."""
        return {name for state_key in self.states for name in self.triggerable_signal_names(state_key)}

    def evaluate_triggers(self, state_key: str, scope: dict[str, Any]) -> str | None:
        action = self.evaluate_triggers_action(state_key, scope)
        return action.name if action else None

    def evaluate_triggers_action(self, state_key: str, scope: dict[str, Any]) -> Action | None:
        """Returns the first action (YAML order) whose trigger evaluates
        true — FIFO priority — or None. Actions without `trigger` stay
        manual-only, never returned here. `scope`: see
        tracking.evaluation_scope.EvaluationScopeBuilder — the
        signal/env/system/session namespaces plus any referenced metric."""
        state = self.states[state_key]
        for action in state.actions:
            if action.trigger and self._eval_trigger(action.trigger, scope):
                return action
        return None

    def preview_triggers(self, state_key: str, scope: dict[str, Any]) -> list:
        """Every triggerable action in `state_key` with its expression and
        evaluation result, in FIFO priority order — for UI display only,
        never applies a transition."""
        state = self.states[state_key]
        results = []
        winner_found = False
        for action in state.actions:
            if not action.trigger:
                continue
            result = self._eval_trigger(action.trigger, scope)
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
    def eval_action_env(action: Action, scope: dict[str, Any]) -> dict[str, Any]:
        """`action`'s own `env` expressions (see automaton_builder.py's
        _build_action), evaluated against `scope` (see
        tracking.evaluation_scope.EvaluationScopeBuilder) — same
        mechanics as _eval_trigger (simpleeval), just without its boolean
        cast: a result here can be any simple value. Unlike _eval_trigger,
        a referenced name that's still None (or missing outright — e.g. a
        typo, or an `env.` key no action has ever set yet) is NOT silently
        treated as a routine no-op: it's left to simpleeval to fail
        naturally, so the exception below always logs it. One key's
        failure never blocks the others in the same `env:` mapping —
        each is caught and logged individually. Returns only the keys
        that evaluated successfully — the caller (tracking.env.Env.
        update_action_set) merges these onto whatever's already stored,
        so a failed key simply leaves its previous value untouched rather
        than being clobbered with a spurious one."""
        if not action.env:
            return {}
        result: dict[str, Any] = {}
        for key, expression in action.env.items():
            try:
                result[key] = simpleeval.simple_eval(expression, names=scope)
            except Exception as exc:
                logger.warning(
                    "env expression evaluation failed for action '%s', key '%s' ('%s'): %s",
                    action.name, key, expression, exc,
                )
        return result

    @staticmethod
    def _eval_trigger(expression: str, scope: dict[str, Any]) -> bool:
        """A malformed expression must never crash the caller: treat
        evaluation failures as False, with a warning. A referenced signal
        that hasn't been computed yet (value None — routine early in a
        conversation, or right after a failed computation) is a distinct,
        expected case: evaluating e.g. `signal.foo > 5` against None would
        always raise, so short-circuit to False without even trying,
        instead of logging a warning for something that isn't wrong. Only
        `signal.*` gets this treatment — env/system/session/metric
        references either resolve to a real value or fail loudly via the
        except below, since none of them has signal's own "not computed
        yet" routine-None case. (Every signal referenced this way is
        guaranteed to be a real declared one by now — see
        automaton_builder.py's _actions_sanity_check at build time — so a
        None here only ever means "not computed yet", never a typo.)
        """
        try:
            signal_values = scope.get("signal", {})
            if any(signal_values.get(name) is None for name in trigger_signal_names(expression)):
                return False
            return bool(simpleeval.simple_eval(expression, names=scope))
        except Exception as exc:
            logger.warning("Trigger evaluation failed for expression '%s': %s", expression, exc)
            return False


