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
    # Not state-level: two different actions landing on the same target
    # state can each carry their own value (or none), since it describes
    # *how you got there*, not the destination itself.
    on_enter: str | None = None
    # {env key: expression source}, evaluated when this action fires and
    # merged onto the env store so the next prompt sees the update. Same
    # scope/mechanics as `trigger` (see _eval_trigger), minus the boolean cast.
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
    # If true, the bot may react to the user's message this turn, choosing
    # from the project's whole `reactions` dict — never a per-state subset
    # (see TurnProtocol's own conditional inclusion of the 'reaction' tag).
    reactions_enabled: bool = False

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
class Reaction:
    """One project-declared reaction a user or the bot can attach to a
    message — same shape as Signal, same reasoning: `ui_description`
    falls back to `definition` when absent (see AutomatonBuilder._build_reaction)."""
    name: str
    ui_label: str
    definition: str
    ui_description: str | None = None


@dataclass
class EnvKey:
    """One project-level `env:` declaration. `value` is the default,
    evaluated once whenever nothing has set the key yet."""
    name: str
    value: str = ""
    ui_description: str | None = None


# Functional syntax (not the class form the other Payload types use):
# "on-enter" isn't a valid Python identifier, so a class body can't declare it.
ActionPayload = TypedDict("ActionPayload", {
    "name": str,
    "ui_label": str,
    "ui_button": str,
    "ui_description": str | None,
    "target": str,
    "has_trigger": bool,
    "on-enter": str | None,
})

class ReactionOptionPayload(TypedDict):
    key: str
    ui_label: str

class StatePayload(TypedDict):
    key: str
    ui_label: str
    ui_description: str | None
    final: bool
    chat: bool
    # The project's whole reaction vocabulary, independent of `key` — a
    # user can react with any of these on any bot message, regardless of
    # which state produced it. See State.reactions_enabled for the bot's
    # own, per-state gated side of this.
    reactions: list[ReactionOptionPayload]
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
    talk_enabled: bool
    signal_tracking_on_ai_message: bool

# Reserved namespaces a trigger/env expression resolves against. `automaton`
# has no entry in _NAMESPACE_PATHS below since automaton.<project>.state/
# env.<key> is a dynamic, per-project chain static-tuple matching can't express.
RESERVED_NAMESPACES = ("signal", "env", "system", "session", "metric", "automaton")

# Dotted sub-namespaces nested one level under a reserved namespace above —
# each entry matches as a *whole* path, so `session.metric.<attr>` and plain
# `session.<attr>` resolve to different namespaces.
NESTED_NAMESPACES = (("session", "metric"),)

_NAMESPACE_PATHS: tuple[tuple[str, ...], ...] = tuple((ns,) for ns in RESERVED_NAMESPACES) + NESTED_NAMESPACES


def _maximal_attribute_nodes(tree: ast.AST) -> list[ast.Attribute]:
    """Every ast.Attribute node in `tree` not nested inside a longer
    attribute chain, so a dotted chain is matched against its full,
    longest namespace path (see _namespace_path_of), never a shorter prefix."""
    nested_value_ids = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    return [node for node in ast.walk(tree) if isinstance(node, ast.Attribute) and id(node) not in nested_value_ids]


def _namespace_path_of(node: ast.Attribute) -> tuple[tuple[str, ...], str] | None:
    """(namespace_path, leaf_attr) for `node` if its full dotted chain,
    root to leaf, is exactly one of _NAMESPACE_PATHS plus one more
    attribute (e.g. `signal.mood` -> (("signal",), "mood")) — None otherwise."""
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
    "signal.daysSinceLastEvent >= 85" -> {"daysSinceLastEvent"}."""
    tree = ast.parse(expression, mode="eval")
    return _namespace_attrs(tree, "signal")


def trigger_bare_names(expression: str) -> set[str]:
    """Every identifier referenced *outside* one of the reserved
    namespaces (see RESERVED_NAMESPACES) — in practice a core metric name.
    A nested-namespace root (see NESTED_NAMESPACES) is excluded too."""
    tree = ast.parse(expression, mode="eval")
    namespace_bases = {
        node.value.id for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in RESERVED_NAMESPACES
    }
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} - namespace_bases


def trigger_automaton_project_refs(expression: str) -> set[str]:
    """Every project name referenced as `automaton.<project>...` in
    `expression`. Walks every Attribute node (not just maximal ones),
    since a reference is meaningful at any depth in the chain."""
    tree = ast.parse(expression, mode="eval").body
    return {
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "automaton"
    }


def trigger_automaton_env_refs(expression: str) -> dict[str, set[str]]:
    """Every `automaton.<project>.env.<key>` reference in `expression`,
    grouped by project. Only matches the specific 4-level chain, unlike
    the broader trigger_automaton_project_refs."""
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
    """Every namespace attribute reference in `expression`, keyed by its
    dotted path (e.g. "session.metric"), one entry per namespace actually
    used — a namespace nothing references is absent, never an empty set."""
    tree = ast.parse(expression, mode="eval")
    refs: dict[str, set[str]] = {}
    for node in _maximal_attribute_nodes(tree):
        ref = _namespace_path_of(node)
        if ref is None:
            continue
        path, leaf = ref
        refs.setdefault(".".join(path), set()).add(leaf)
    return refs


# Every identifier whose runtime *type* is fixed by its own contract, well
# enough to check statically. `env.*` is absent: it's a free-form store any
# expression can set to anything, so its type is treated as unknown.
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
# Every identifier under these namespaces is always a number, no per-name
# exceptions to look up — signals by contract, metrics because
# BaseMetric.result always clamps into [0, 100] as a float.
_ALWAYS_NUMERIC_NAMESPACES = (("signal",), ("session", "metric"), ("metric",))

_ORDERING_OPS: dict[type, str] = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}


def _leaf_kind(node: ast.AST) -> str | None:
    """`node`'s statically-known kind ('number'/'string'/'bool'), or None
    (unknown, never "wrong") for a bare name, `env.*` reference, or
    sub-expression whose type isn't knowable ahead of a real turn."""
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
    """Every ordering comparison (`<`/`<=`/`>`/`>=`, never `==`/`!=`) in
    `expression` between operands whose statically-known kinds (see
    _leaf_kind) are incompatible, e.g. `system.today() >= 5`. Returns messages, never raises."""
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
        # Only AutomatonBuilder.build parses a project's declared env:
        # section and passes a real list; other construction sites have none.
        env_keys: list[EnvKey] | None = None,
        # Same reasoning as env_keys above — only AutomatonBuilder.build
        # parses a project's declared reactions: section.
        reactions: list[Reaction] | None = None,
        # The optional top-level `project:` section. `project_id` is the
        # identifier this project is reachable as from other projects'
        # automaton.* references, globally unique (enforced by ProjectService).
        project_id: str | None = None,
        project_ui_label: str | None = None,
        project_ui_description: str | None = None,
        talk_enabled: bool = True,
    ):
        # A real Action (not just a target state string) so it can also
        # carry an action_prompt — see ChatService.open_if_needed.
        self.init_action = init_action
        self.states = states
        self.general_prompt = general_prompt
        self.signals = signals
        self.reactions = reactions or []
        self.env_keys = env_keys or []
        self.project_id = project_id
        self.project_ui_label = project_ui_label
        self.project_ui_description = project_ui_description
        self.general_attachments = general_attachments
        self.attachments = attachments
        # The two auto-tracking modes (before/after the AI reply) are
        # mutually exclusive — this flag selects between them.
        self.autotracking_on_ai_message = autotracking_on_ai_message
        self.talk_enabled = talk_enabled

    def get_state(self, state_key: str) -> State:
        return self.states[state_key]

    @staticmethod
    def get_action_payload(action: Action) -> ActionPayload:
        """Serializes `action` for the frontend. Deliberately omits
        `trigger`'s raw expression — that's internal transition logic,
        only ever exposed via the "Edit project" view's Inspect panel."""
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
        """Serializes `signal` for the frontend. `attachments` stays
        empty deliberately: shipping full (base64) file content on every
        call would be wasteful when only the names are usually needed."""
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
        """Serializes `env_key` for the frontend — mirrors
        get_signal_payload's role for Signal."""
        return {
            "name": env_key.name,
            "ui_description": env_key.ui_description,
            "value": env_key.value,
        }

    @staticmethod
    def get_reaction_option_payload(reaction: Reaction) -> ReactionOptionPayload:
        """Serializes `reaction` for the frontend's reaction picker —
        deliberately omits definition/ui_description, the AI-facing
        fields that decide when the bot itself would use it, same
        reasoning as get_action_payload's own omission of `trigger`."""
        return {"key": reaction.name, "ui_label": reaction.ui_label}

    def get_state_payload(self, state: State) -> StatePayload:
        """Serializes `state` for the frontend. Safety barrier: the
        reserved implicit state ("") must never reach a caller outside
        ChatService.open_if_needed. Not static, unlike its siblings above:
        `reactions` is this automaton's own whole vocabulary, not
        something `state` itself carries."""
        if state.key == "":
            raise RuntimeError("Refusing to serialize the implicit initial state ('').")
        return {
            "key": state.key,
            "ui_label": state.ui_label,
            "ui_description": state.ui_description,
            "final": state.final,
            "chat": state.chat,
            "reactions": [self.get_reaction_option_payload(r) for r in self.reactions],
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
        """Whether any triggerable action leaving `state_key` references
        one of `names` as a *bare* identifier — in practice a metric
        name. Lets a caller skip resolving an expensive value set when nothing needs it."""
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
        """triggerable_signal_names, unioned across every state — the
        project-wide "is this signal used anywhere" view (backs the
        Inspector Signals tab's "relevant signals" filter)."""
        return {name for state_key in self.states for name in self.triggerable_signal_names(state_key)}

    def evaluate_triggers(self, state_key: str, scope: dict[str, Any]) -> str | None:
        action = self.evaluate_triggers_action(state_key, scope)
        return action.name if action else None

    def evaluate_triggers_action(self, state_key: str, scope: dict[str, Any]) -> Action | None:
        """Returns the first action (YAML order) whose trigger evaluates
        true — FIFO priority — or None. Actions without `trigger` stay
        manual-only, never returned here."""
        state = self.states[state_key]
        for action in state.actions:
            if action.trigger and self._eval_trigger(action.trigger, scope):
                return action
        return None

    @staticmethod
    def eval_action_env(action: Action, scope: dict[str, Any]) -> dict[str, Any]:
        """`action`'s `env` expressions evaluated against `scope`. Unlike
        _eval_trigger, a None/missing reference fails and logs rather
        than being a no-op; only successfully evaluated keys are returned."""
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
        """A malformed expression never crashes the caller: failures
        return False with a warning. A `signal.*` still None (not
        computed yet) short-circuits to False silently instead."""
        try:
            signal_values = scope.get("signal", {})
            if any(signal_values.get(name) is None for name in trigger_signal_names(expression)):
                return False
            return bool(simpleeval.simple_eval(expression, names=scope))
        except Exception as exc:
            logger.warning("Trigger evaluation failed for expression '%s': %s", expression, exc)
            return False


