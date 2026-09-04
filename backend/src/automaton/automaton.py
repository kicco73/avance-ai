"""YAML parsing for the DFA definition and in-memory data structures."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

import simpleeval

from automaton.scope import EvaluationScope

from logging_factory import LoggerFactory

from .trigger_expression_analyzer import TriggerExpressionAnalyzer

logger = LoggerFactory.get_logger(__name__)

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


@dataclass
class Source:
    """One `sources:` declaration — binds `name` (what `source.<name>.*`
    calls in a trigger/env: expression resolve against, see
    tracking.sources.SourceNamespace) to a driver and its own target,
    both encoded in `url` (`<scheme>:<path>`, e.g.
    'avance:behaviour/flights.csv' — see tracking.sources.url)."""
    name: str
    url: str
    ui_label: str
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

def manual_actions_for(actions: list[ActionPayload], auto_tracking_enabled: bool) -> list[ActionPayload]:
    return [a for a in actions if not a["has_trigger"] or not auto_tracking_enabled]


class JsSnippet(str):
    # FIXME: subclassing str, not a plain str, is load-bearing —
    # render_on_enter uses isinstance(result, JsSnippet) to tell an
    # actuator's wire-ready JS apart from actuator.prompt()'s plain text.
    pass


class DeferredExpression(object):
    """What a zero-argument `lambda:` in an on-enter line evaluates to:
    a callable closing over the evaluator (hence its scope) and the
    lambda's body, exactly like the plain closure it replaces — but one
    that also *knows its own source* (`source`, the body re-emitted by
    ast.unparse) and the EvaluationScope it was built against. Those two
    are what let actuator.defer hibernate the call instead of holding a
    live closure (see tracking/actuators/on_enter_task.py)."""

    def __init__(self, evaluator: "_OnEnterEval", body: ast.expr) -> None:
        self._evaluator = evaluator
        self._body = body
        self.source: str = ast.unparse(body)

    @property
    def scope(self) -> EvaluationScope:
        return self._evaluator.names

    def __call__(self):
        return self._evaluator._eval(self._body)

    def __repr__(self) -> str:
        return f"DeferredExpression({self.source!r})"


class _OnEnterEval(simpleeval.SimpleEval):
    """Evaluates one on-enter line. Only ever against an EvaluationScope
    — a plain dict has no automaton/state to hibernate a deferred call
    with, so it is refused up front rather than failing at defer time."""

    names: EvaluationScope

    def __init__(self, names: EvaluationScope) -> None:
        if not isinstance(names, EvaluationScope):
            raise TypeError(f"_OnEnterEval needs an EvaluationScope, got {type(names).__name__}.")
        super().__init__(names=names)
        self.nodes[ast.Lambda] = self._eval_lambda

    def _eval_lambda(self, node: ast.Lambda):
        if node.args.args or node.args.vararg or node.args.kwonlyargs or node.args.kwarg or node.args.posonlyargs:
            raise simpleeval.FeatureNotAvailable("Sorry, only zero-argument lambdas are supported.")
        return DeferredExpression(self, node.body)

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

class SourcePayload(TypedDict):
    name: str
    ui_label: str
    ui_description: str | None
    url: str

class ProjectPayload(TypedDict):
    id: str
    family: str | None
    revision: int
    ui_label: str | None
    ui_description: str | None
    talk_enabled: bool
    signal_tracking_on_ai_message: bool
    general_prompt: str


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
        # Same reasoning again — only AutomatonBuilder.build parses a
        # project's declared sources: section. Empty (never None) unless
        # a project actually declares one — see tracking.sources.SourceNamespace.
        sources: list[Source] | None = None,
        # The optional top-level `project:` section. `project_id` is this
        # project's own mandatory, globally unique identity — what
        # *other* projects reach it as through automaton.* references,
        # and the sole key every DB table stores it under (see
        # db/models.py's Project.id). `project_family` gates that
        # visibility: two projects can observe/notify each other only
        # when both declare the exact same family (never parsed, plain
        # string equality) — None (the default) means neither observes
        # nor is observed by anything, itself included (see
        # AutomatonLoader.known_projects_env_keys). `project_revision` is
        # the YAML's own declared `project.revision` (default 0) —
        # distinct from this object's own `revision` attribute below
        # (which DB storage revision it was actually loaded from).
        project_id: str | None = None,
        project_family: str | None = None,
        project_revision: int = 0,
        project_ui_label: str | None = None,
        project_ui_description: str | None = None,
        talk_enabled: bool = True,
    ):
        # A real Action (not just a target state string) so it can also
        # carry its own on_enter/env — see ChatService._ensure_project_bootstrap.
        self.init_action = init_action
        self.states = states
        self.general_prompt = general_prompt
        self.signals = signals
        self.reactions = reactions or []
        self.env_keys = env_keys or []
        self.sources = sources or []
        self.project_id = project_id
        self.family = project_family
        self.project_revision = project_revision
        self.project_ui_label = project_ui_label
        self.project_ui_description = project_ui_description
        self.general_attachments = general_attachments
        self.attachments = attachments
        # The two auto-tracking modes (before/after the AI reply) are
        # mutually exclusive — this flag selects between them.
        self.autotracking_on_ai_message = autotracking_on_ai_message
        self.talk_enabled = talk_enabled
        # Which DB storage revision this Automaton actually came from —
        # unset here (never a build()-time concern: most callers,
        # including nearly every test, build one purely in-memory with
        # nothing to pin). Only AutomatonLoader.load_at_revision and
        # ProjectManager.finalize_update, the two places that resolve
        # this correctly and are about to cache the result, ever call
        # set_storage_location below. project_id above already carries
        # this project's own identity, so there's nothing left to pass in.
        self.revision: int | None = None

    def set_storage_location(self, revision: int) -> None:
        """tracking.sources.avance_archive's own AvanceArchiveSource reads
        straight from Db at this exact (project_id, revision) — never
        this Automaton's own in-memory `attachments` (see that module's
        docstring for why: a large file no state/action/signal ever
        declared under its own `attachments:` would otherwise still get
        eagerly loaded/converted on every build just because it's in the
        project, whether any source ever reads it or not)."""
        self.revision = revision

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
    def get_source_payload(source: Source) -> SourcePayload:
        """Serializes `source` for the frontend — mirrors
        get_env_key_payload's role for EnvKey."""
        return {
            "name": source.name,
            "ui_label": source.ui_label,
            "ui_description": source.ui_description,
            "url": source.url,
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

    def reactions_enabled_for(self, state: State) -> bool:
        """Whether the bot itself may actually attach a reaction while in
        `state` — `state.reactions_enabled` opting in is necessary but not
        sufficient: with no `reactions:` declared at all there's no
        vocabulary to tag from, so the flag has no effect regardless of
        what the state itself says (see TrackingProcessor.
        build_turn_protocol/estimate_state_prompt, the two callers)."""
        return state.reactions_enabled and bool(self.reactions)

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
            action.trigger and TriggerExpressionAnalyzer.bare_names(action.trigger) & names for action in state.actions
        )

    def triggerable_signal_names(self, state_key: str) -> set[str]:
        state = self.states[state_key]
        referenced: set[str] = set()
        for action in state.actions:
            if action.trigger:
                referenced |= TriggerExpressionAnalyzer.signal_names(action.trigger)
            if action.env:
                for expression in action.env.values():
                    referenced |= TriggerExpressionAnalyzer.signal_names(expression)
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
    def render_on_enter(action: Action, scope: EvaluationScope) -> str | None:
        """Evaluates `action.on_enter` — the same namespaced-expression
        grammar as `trigger`/`env` (one `actuator.<name>(...)` call per
        top-level statement, e.g. `actuator.celebrate()` /
        `actuator.notify(user.name, "Hi!")`, split via
        TriggerExpressionAnalyzer.on_enter_statements so a single call may
        itself span several lines and a '#' comment needs no special
        handling) — into the wire-ready JS text the frontend's
        onEnterActions.js already knows how to run unchanged: each
        statement's own return value is tunneled through verbatim only
        when it's a JsSnippet (`celebrate()`, `notify(...)`, and `show(...)`
        compile to themselves, minus the "actuator." prefix) — a plain
        `str` (e.g. a bare `actuator.prompt(...)` statement's own reply
        text, never wrapped by another actuator call) or None (a pure
        server-side side effect, e.g. `send_mail`) both contribute
        nothing. A statement may instead be a simple `name = <expr>`
        assignment (see TriggerExpressionAnalyzer.on_enter_assignment):
        `<expr>` is evaluated the same way but its result is stored under
        `name` directly on `actuator_scope` — never appended to
        `snippets`, even when it's a JsSnippet — so every later statement
        in this same on_enter can reference `name` bare (including inside
        an actuator.defer(...) lambda, which shares this same evaluator/
        scope — see DeferredExpression.scope and freeze()'s own "extra"
        capture, which already snapshots any such bare scalar). A
        statement that fails to evaluate is logged and simply contributes
        nothing (an assignment that fails leaves `name` unset, so a later
        reference to it fails too, same way any other undefined name
        would) — this only ever affects on_enter as a whole (rather than
        one statement of it) when it fails to parse at all, which
        build-time validation already rules out for any project this ever
        runs against."""
        if not action.on_enter:
            return None
        return Automaton.render_on_enter_script(action.on_enter, scope.for_actuators(action_name=action.name))

    @staticmethod
    def render_on_enter_script(script: str, actuator_scope: EvaluationScope) -> str | None:
        """render_on_enter's own engine, on a bare script and an already
        actuator-view scope — also what an OnEnterTask runs, later and
        possibly in another process, against a rehydrated scope (see
        tracking/actuators/on_enter_task.py): the same code path whether
        the on-enter fires now or was deferred."""
        action_name = actuator_scope.action_name
        try:
            statements = TriggerExpressionAnalyzer.on_enter_statements(script)
        except SyntaxError as exc:
            logger.warning("on-enter parsing failed for action '%s': %s", action_name, exc)
            return None
        snippets = []
        for _line_number, statement in statements:
            assignment = TriggerExpressionAnalyzer.on_enter_assignment(statement)
            target, expression = assignment if assignment is not None else (None, statement)
            try:
                result = _OnEnterEval(names=actuator_scope).eval(expression)
            except Exception as exc:
                logger.warning(
                    "on-enter expression evaluation failed for action '%s' ('%s'): %s",
                    action_name, statement, exc,
                )
                continue
            if target is not None:
                actuator_scope[target] = result
            elif isinstance(result, JsSnippet):
                snippets.append(result)
        return "\n".join(snippets) if snippets else None

    @staticmethod
    def _eval_trigger(expression: str, scope: dict[str, Any]) -> bool:
        """A malformed expression never crashes the caller: failures
        return False with a warning. A `signal.*` still None (not
        computed yet) short-circuits to False silently instead."""
        try:
            signal_values = scope.get("signal", {})
            if any(signal_values.get(name) is None for name in TriggerExpressionAnalyzer.signal_names(expression)):
                return False
            return bool(simpleeval.simple_eval(expression, names=scope))
        except Exception as exc:
            logger.warning("Trigger evaluation failed for expression '%s': %s", expression, exc)
            return False


