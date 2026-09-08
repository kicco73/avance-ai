"""CoreAutomaton — everything an automaton is, minus everything only
the platform around it asks of one.

What is here: the declared data (states, signals, sources, env keys,
prompts, project identity), the trivial lookups over it, the answers
derived from its own expression text (computed once, see analysis.py),
and the four seams where an expression actually gets evaluated —
_eval_trigger/evaluate_triggers_action, eval_action_env,
eval_action_on_exit, render_task/render_task_script.

Those four are the whole of what an automaton compiled to literal Python
replaces. Everything else it inherits unchanged, which is why this
module imports nothing from the platform: no payload serialization, no
introspection for the design view, no Db, no project service. See
payloads.py and introspection.py for the contracts that are composed on
top, and automaton.py for the composition itself."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

import simpleeval

from automaton.scope import EvaluationScope

from logging_factory import LoggerFactory

from . import analysis
from .trigger_expression_analyzer import TriggerExpressionAnalyzer

if TYPE_CHECKING:
    from .model import Action, EnvKey, MemoryArchive, Reaction, Signal, Source, State

logger = LoggerFactory.get_logger(__name__)

class JsSnippet(str):
    # FIXME: subclassing str, not a plain str, is load-bearing —
    # render_task/eval_action_on_exit use isinstance(result, JsSnippet)
    # to tell a task/chat call's wire-ready JS apart from task.prompt()'s
    # plain text.
    pass


class DeferredExpression(object):
    """What a zero-argument `lambda:` in a task line evaluates to:
    a callable closing over the evaluator (hence its scope) and the
    lambda's body, exactly like the plain closure it replaces — but one
    that also *knows its own source* (`source`, the body re-emitted by
    ast.unparse) and the EvaluationScope it was built against. Those two
    are what let task.defer hibernate the call instead of holding a
    live closure (see tracking/actuators/action_task.py)."""

    def __init__(self, evaluator: "_TaskEval", body: ast.expr) -> None:
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


class _TaskEval(simpleeval.EvalWithCompoundTypes):
    """Evaluates one task line. Only ever against an EvaluationScope
    — a plain dict has no automaton/state to hibernate a deferred call
    with, so it is refused up front rather than failing at defer time."""

    names: EvaluationScope

    def __init__(self, names: EvaluationScope) -> None:
        if not isinstance(names, EvaluationScope):
            raise TypeError(f"_TaskEval needs an EvaluationScope, got {type(names).__name__}.")
        super().__init__(names=names)
        self.nodes[ast.Lambda] = self._eval_lambda

    def _eval_lambda(self, node: ast.Lambda):
        if node.args.args or node.args.vararg or node.args.kwonlyargs or node.args.kwarg or node.args.posonlyargs:
            raise simpleeval.FeatureNotAvailable("Sorry, only zero-argument lambdas are supported.")
        return DeferredExpression(self, node.body)


class CoreAutomaton(object):

    def __init__(
        self,
        init_action: Action,
        states: dict[str, State],
        general_prompt: str,
        signals: list[Signal],
        # Stored paths of the project's own top-level `attachments:`,
        # resolved at build time — see model.Action.attachments.
        general_attachments: tuple[str, ...],
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
        # "resume" (default): a brand-new live session picks up wherever
        # this user's own live automaton state already is (LiveSessionStrategy.
        # starting_state). "restart": it enters cold instead, exactly like a
        # test/preview session does — see SessionTypeStrategy.
        # _init_action_start (session_type_strategy.py) — the target/task
        # pair every strategy that starts a session at project boot shares.
        new_session_strategy: str = "resume",
        # Non-fatal findings AutomatonBuilder.build collected while
        # validating this project — a configuration that builds and runs
        # but almost certainly isn't what the author meant (see
        # AutomatonBuilder._actions_sanity_check). Never populated for an
        # Automaton built in-memory by hand.
        build_warnings: list[str] | None = None,
    ):
        # A real Action (not just a target state string) so it can also
        # carry its own task/env — see ChatService._ensure_project_bootstrap.
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
        self.general_attachments = tuple(general_attachments)
        # The two auto-tracking modes (before/after the AI reply) are
        # mutually exclusive — this flag selects between them.
        self.autotracking_on_ai_message = autotracking_on_ai_message
        self.talk_enabled = talk_enabled
        self.new_session_strategy = new_session_strategy
        self.build_warnings = list(build_warnings or [])
        # Which DB storage revision this Automaton actually came from —
        # unset here (never a build()-time concern: most callers,
        # including nearly every test, build one purely in-memory with
        # nothing to pin). Only AutomatonLoader.load_at_revision and
        # ProjectManager.finalize_update, the two places that resolve
        # this correctly and are about to cache the result, ever call
        # set_storage_location below. project_id above already carries
        # this project's own identity, so there's nothing left to pass in.
        self.revision: int | None = None
        # Where this automaton's own project files live, for an automaton
        # that carries them rather than pointing at a database: a compiled
        # package sets it to its own data/ directory (see
        # tracking.project_files.project_files_for, the one place it is
        # read). None on the platform, where `revision` above says where
        # to read instead.
        self.archives_dir: "Path | None" = None
        # Answers derived from this automaton's own expression text (see
        # analysis.py), each computed on first use and kept: they are
        # fixed the moment the project is written, and `states`/`actions`
        # are never mutated after construction — a change to a project
        # builds a new Automaton (AutomatonBuilder.build), it never edits
        # one in place. Kept per part rather than as one bundle so each
        # keeps raising, or not raising, exactly where it used to.
        self._declared_env_key_names: set[str] | None = None
        self._triggerable_signal_names: dict[str, set[str]] = {}
        self._trigger_bare_names: dict[str, set[str]] = {}

    def set_storage_location(self, revision: int) -> None:
        """Which stored revision this automaton's own files are read at.
        Everything that reads one — a source with an `avance:` url,
        attachment.read, a turn's own attachments — goes through
        tracking.project_files.ProjectFiles, and this is what tells it
        where to look when the automaton points at a database rather than
        carrying its files (see archives_dir above)."""
        self.revision = revision

    def get_state(self, state_key: str) -> State:
        return self.states[state_key]

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

    def declared_env_key_names(self) -> set[str]:
        if self._declared_env_key_names is None:
            self._declared_env_key_names = analysis.declared_env_key_names(
                self.env_keys, self.init_action, self.states,
            )
        # A copy: callers get a set of their own to mutate, exactly as
        # when this was recomputed from scratch on every call.
        return set(self._declared_env_key_names)

    def triggerable_signal_names(self, state_key: str) -> set[str]:
        cached = self._triggerable_signal_names.get(state_key)
        if cached is None:
            cached = analysis.triggerable_signal_names(
                self.states[state_key], {signal.name for signal in self.signals},
            )
            self._triggerable_signal_names[state_key] = cached
        return set(cached)

    def evaluate_triggers_action(self, state_key: str, scope: dict[str, Any]) -> Action | None:
        """Returns the first action (YAML order) whose trigger evaluates
        true — FIFO priority — or None. Actions without `trigger` stay
        manual-only, never returned here."""
        state = self.states[state_key]
        for action in state.actions:
            if action.trigger and self._eval_trigger(action.trigger, scope):
                return action
        return None

    @classmethod
    def eval_action_env(cls, action: "Action", scope: dict[str, Any]) -> dict[str, Any]:
        """`action`'s `env` expressions evaluated against `scope`. Unlike
        _eval_trigger, a None/missing reference fails and logs rather
        than being a no-op; only successfully evaluated keys are returned."""
        if not action.env:
            return {}
        result: dict[str, Any] = {}
        for key, expression in action.env.items():
            try:
                result[key] = cls._evaluate_expression(expression, scope)
            except Exception as exc:
                logger.warning(
                    "env expression evaluation failed for action '%s', key '%s' ('%s'): %s",
                    action.name, key, expression, exc,
                )
        return result

    @classmethod
    def eval_action_on_exit(cls, action: "Action", scope: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        """`action.on_exit`'s own mixed grammar, evaluated against
        `scope` and split into statements with task's own grammar
        (TriggerExpressionAnalyzer.task_statements) so on-exit reads
        exactly like task: one statement per line, a single call may
        span several lines, and a '#' comment just works. Each
        statement is either an `env.<key> = expr` assignment
        (TriggerExpressionAnalyzer.on_exit_assignment — eval_action_env's
        own contract: only successfully evaluated keys are returned, a
        bad expression logs and is skipped) or a bare `chat.<method>(...)`
        call, evaluated the same way render_task_script evaluates a
        task line's own non-assignment statement — its return value is
        collected only when it's a JsSnippet, everything else
        contributes nothing. A statement that's neither a valid
        assignment nor a `chat.*` call producing a JsSnippet is logged
        and skipped, never raised — build-time validation
        (AutomatonValidator.validate_on_exit) already rules out anything
        else reaching here. Note what on-exit still can't do:
        task.*'s own send_mail/whatsapp/defer/prompt remain task's job
        alone — on-exit may only write env and call chat.*. Returns
        (env_updates, joined_chat_snippets_or_None), the second element
        exactly `render_task_script`'s own final line."""
        if not action.on_exit:
            return {}, None
        try:
            statements = TriggerExpressionAnalyzer.task_statements(action.on_exit)
        except SyntaxError as exc:
            logger.warning("on-exit parsing failed for action '%s': %s", action.name, exc)
            return {}, None
        result: dict[str, Any] = {}
        snippets: list[str] = []
        for _line_number, statement in statements:
            assignment = TriggerExpressionAnalyzer.on_exit_assignment(statement)
            if assignment is not None:
                key, expression = assignment
                try:
                    result[key] = cls._evaluate_expression(expression, scope)
                except Exception as exc:
                    logger.warning(
                        "on-exit expression evaluation failed for action '%s', key '%s' ('%s'): %s",
                        action.name, key, expression, exc,
                    )
                continue
            try:
                value = cls._evaluate_statement(statement, scope)
            except Exception as exc:
                logger.warning(
                    "on-exit expression evaluation failed for action '%s' ('%s'): %s",
                    action.name, statement, exc,
                )
                continue
            if isinstance(value, JsSnippet):
                snippets.append(value)
            else:
                logger.warning(
                    "on-exit statement ignored for action '%s': '%s' is neither an 'env.<key> = expr' "
                    "assignment nor a 'chat.<method>(...)' call.",
                    action.name, statement,
                )
        return result, ("\n".join(snippets) if snippets else None)

    @classmethod
    def render_task(cls, action: "Action", scope: EvaluationScope) -> str | None:
        """Evaluates `action.task` — the same namespaced-expression
        grammar as `trigger`/`env` (one `task.<name>(...)` call per
        top-level statement, e.g. `task.send_mail(user.email, "Hi!")`,
        split via TriggerExpressionAnalyzer.task_statements so a single
        call may itself span several lines and a '#' comment needs no
        special handling) — into the wire-ready JS text the frontend's
        taskActions.js already knows how to run unchanged: each
        statement's own return value is tunneled through verbatim only
        when it's a JsSnippet — a plain `str` (e.g. a bare
        `task.prompt(...)` statement's own reply text, never wrapped by
        another task call) or None (a pure server-side side effect,
        e.g. `send_mail`) both contribute nothing. A statement may
        instead be a simple `name = <expr>` assignment (see
        TriggerExpressionAnalyzer.task_assignment): `<expr>` is
        evaluated the same way but its result is stored under `name`
        directly on `task_scope` — never appended to `snippets`, even
        when it's a JsSnippet — so every later statement in this same
        task can reference `name` bare (including inside a
        task.defer(...) lambda, which shares this same evaluator/
        scope — see DeferredExpression.scope and freeze()'s own "extra"
        capture, which already snapshots any such bare scalar). A
        statement that fails to evaluate is logged and simply contributes
        nothing (an assignment that fails leaves `name` unset, so a later
        reference to it fails too, same way any other undefined name
        would) — this only ever affects task as a whole (rather than
        one statement of it) when it fails to parse at all, which
        build-time validation already rules out for any project this ever
        runs against."""
        if not action.task:
            return None
        return cls.render_task_script(action.task, scope.for_task(action_name=action.name))

    @classmethod
    def render_task_script(cls, script: str, task_scope: EvaluationScope) -> str | None:
        """render_task's own engine, on a bare script and an already
        task-view scope — also what an ActionTask runs, later and
        possibly in another process, against a rehydrated scope (see
        tracking/actuators/action_task.py): the same code path whether
        the task fires now or was deferred."""
        action_name = task_scope.action_name
        try:
            statements = TriggerExpressionAnalyzer.task_statements(script)
        except SyntaxError as exc:
            logger.warning("task parsing failed for action '%s': %s", action_name, exc)
            return None
        snippets = []
        for _line_number, statement in statements:
            assignment = TriggerExpressionAnalyzer.task_assignment(statement)
            target, expression = assignment if assignment is not None else (None, statement)
            try:
                result = cls._evaluate_statement(expression, task_scope)
            except Exception as exc:
                logger.warning(
                    "task expression evaluation failed for action '%s' ('%s'): %s",
                    action_name, statement, exc,
                )
                continue
            if target is not None:
                task_scope[target] = result
            elif isinstance(result, JsSnippet):
                snippets.append(result)
        return "\n".join(snippets) if snippets else None

    # --- the seam ---------------------------------------------------------
    # Every expression this automaton evaluates goes through one of the
    # three below, and nothing else here ever touches an evaluator. They
    # are the entire behavioural surface a compiled automaton replaces:
    # override these and the loops above — their ordering, their
    # try/except, their warnings, what they collect and what they skip —
    # stay literally the same code, which is the only way to be sure an
    # interpreted and a compiled automaton cannot drift apart.

    @classmethod
    def _evaluate_expression(cls, expression: str, scope: dict[str, Any]) -> Any:
        """One `trigger`/`env:`/on-exit-assignment expression, evaluated
        against `scope`. Raises whatever evaluation raises; every caller
        above decides for itself what a failure means."""
        return simpleeval.EvalWithCompoundTypes(names=scope).eval(expression)

    @classmethod
    def _evaluate_statement(cls, statement: str, scope: dict[str, Any]) -> Any:
        """One task or on-exit statement — the same as
        _evaluate_expression except that a zero-argument lambda is
        allowed, which is what task.defer(...) is built out of."""
        return _TaskEval(names=scope).eval(statement)

    @classmethod
    def _referenced_signal_names(cls, expression: str) -> set[str]:
        """Which declared signals `expression` reads — asked before
        evaluating a trigger, so that one still None short-circuits to
        False silently instead of raising."""
        return TriggerExpressionAnalyzer.signal_names(expression)

    @classmethod
    def _eval_trigger(cls, expression: str, scope: dict[str, Any]) -> bool:
        """A malformed expression never crashes the caller: failures
        return False with a warning. A `signal.*` still None (not
        computed yet) short-circuits to False silently instead."""
        try:
            signal_values = scope.get("signal", {})
            if any(signal_values.get(name) is None for name in cls._referenced_signal_names(expression)):
                return False
            return bool(cls._evaluate_expression(expression, scope))
        except Exception as exc:
            logger.warning("Trigger evaluation failed for expression '%s': %s", expression, exc)
            return False
