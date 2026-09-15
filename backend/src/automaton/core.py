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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import simpleeval

from automaton.env_types import ENV_TYPES, UNDECLARED_ENV_TYPE
from automaton.model import env_defaults_action
from automaton.project_services import ProjectServices
from automaton.scope import EvaluationScope

from system.logging_factory import LoggerFactory

from . import analysis
from .trigger_expression_analyzer import TriggerExpressionAnalyzer

if TYPE_CHECKING:
    from .model import Action, EnvKey, MemoryArchive, Reaction, Signal, Source, State

logger = LoggerFactory.get_logger(__name__)

class JsSnippet(str):
    # FIXME: subclassing str, not a plain str, is load-bearing —
    pass


@dataclass(frozen=True)
class TaskOutcome:

    snippets: str | None = None
    failures: tuple[tuple[str, Exception], ...] = ()


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
        general_attachments: tuple[str, ...],
        autotracking_on_ai_message: bool,
        env_keys: list[EnvKey] | None = None,
        reactions: list[Reaction] | None = None,
        sources: list[Source] | None = None,
        project_id: str | None = None,
        project_family: str | None = None,
        project_revision: int = 0,
        project_ui_label: str | None = None,
        project_ui_description: str | None = None,
        project_services: dict[str, str] | None = None,
        new_session_strategy: str = "resume",
    ):
        self.init_action = init_action
        self.states = states
        self.general_prompt = general_prompt
        self.signals = signals
        self.reactions = reactions or []
        self.env_keys = env_keys or []
        self.env_defaults_action = env_defaults_action(self.env_keys)
        self._env_types = {env_key.name: ENV_TYPES[env_key.type] for env_key in self.env_keys}
        self.sources = sources or []
        self.project_id = project_id
        self.family = project_family
        self.project_revision = project_revision
        self.project_ui_label = project_ui_label
        self.project_ui_description = project_ui_description
        self.general_attachments = tuple(general_attachments)
        self.autotracking_on_ai_message = autotracking_on_ai_message
        self.services = ProjectServices(project_services)
        self.new_session_strategy = new_session_strategy
        self.revision: int | None = None
        self.archives_dir: "Path | None" = None
        self._declared_env_key_names: set[str] | None = None
        self._tracked_signal_names: dict[str, set[str]] = {}
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
        return set(self._declared_env_key_names)

    def tracked_signal_names(self, state_key: str) -> set[str]:
        cached = self._tracked_signal_names.get(state_key)
        if cached is None:
            cached = analysis.tracked_signal_names(
                self.states[state_key], {signal.name for signal in self.signals},
            )
            self._tracked_signal_names[state_key] = cached
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

    def _accepts_env_value(self, action: "Action", key: str, value: Any) -> bool:
        env_type = self._env_types.get(key, UNDECLARED_ENV_TYPE)
        if env_type.accepts(value):
            return True
        logger.warning(
            "env value discarded for action '%s', key '%s': declared %s, got %s (%r)",
            action.name, key, env_type.name, type(value).__name__, value,
        )
        return False

    def eval_action_env(self, action: "Action", scope: dict[str, Any]) -> dict[str, Any]:
        """`action`'s `env` expressions evaluated against `scope`. Unlike
        _eval_trigger, a None/missing reference fails and logs rather
        than being a no-op; only successfully evaluated keys are returned."""
        if not action.env:
            return {}
        result: dict[str, Any] = {}
        for key, expression in action.env.items():
            try:
                value = self._evaluate_expression(expression, scope)
            except Exception as exc:
                logger.warning(
                    "env expression evaluation failed for action '%s', key '%s' ('%s'): %s",
                    action.name, key, expression, exc,
                )
                continue
            if self._accepts_env_value(action, key, value):
                result[key] = value
        return result

    def eval_action_on_exit(self, action: "Action", scope: EvaluationScope) -> tuple[dict[str, Any], str | None]:
        """`action.on_exit`'s own mixed grammar, evaluated against
        `scope` and split into statements with task's own grammar
        (TriggerExpressionAnalyzer.task_statements) so on-exit reads
        exactly like task: one statement per line, a single call may
        span several lines, and a '#' comment just works. Each
        statement is an `env.<key> = expr` assignment
        (TriggerExpressionAnalyzer.on_exit_assignment — eval_action_env's
        own contract: only successfully evaluated keys are returned, a
        bad expression logs and is skipped), a `name = expr` local
        (TriggerExpressionAnalyzer.task_assignment — stored on the
        script's own scope view, see EvaluationScope.for_on_exit, so a
        later line reads it bare, and gone once the script ends), or a
        bare `chat.<method>(...)` call, evaluated the same way
        render_task_script evaluates a task line's own non-assignment
        statement — its return value is collected only when it's a
        JsSnippet, everything else contributes nothing. A statement
        that's none of those is logged and skipped, never raised —
        build-time validation (AutomatonValidator.validate_on_exit)
        already rules out anything else reaching here. Note what on-exit
        still can't do: task.*'s own send_mail/whatsapp/defer/prompt
        remain task's job alone. Returns (env_updates,
        joined_chat_snippets_or_None), the second element exactly what
        `render_task_script` puts in its own `snippets`."""
        if not action.on_exit:
            return {}, None
        try:
            statements = TriggerExpressionAnalyzer.task_statements(action.on_exit)
        except SyntaxError as exc:
            logger.warning("on-exit parsing failed for action '%s': %s", action.name, exc)
            return {}, None
        scope = scope.for_on_exit(action.name)
        result: dict[str, Any] = {}
        snippets: list[str] = []
        for _line_number, statement in statements:
            assignment = TriggerExpressionAnalyzer.on_exit_assignment(statement)
            local = TriggerExpressionAnalyzer.task_assignment(statement)
            if assignment is not None or local is not None:
                key, expression = assignment or local
                try:
                    value = self._evaluate_expression(expression, scope)
                except Exception as exc:
                    logger.warning(
                        "on-exit expression evaluation failed for action '%s', key '%s' ('%s'): %s",
                        action.name, key, expression, exc,
                    )
                    continue
                if assignment is None:
                    scope[key] = value
                elif self._accepts_env_value(action, key, value):
                    result[key] = value
                continue
            try:
                value = self._evaluate_statement(statement, scope)
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
    def render_task(cls, action: "Action", scope: EvaluationScope) -> TaskOutcome:
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
        statement that fails to evaluate is logged, recorded in the
        outcome's `failures` and contributes nothing else (an assignment
        that fails leaves `name` unset, so a later reference to it fails
        too, same way any other undefined name would) — the script as a
        whole only fails when it does not parse, which build-time
        validation already rules out for any project this ever runs
        against."""
        if not action.task:
            return TaskOutcome()
        return cls.render_task_script(action.task, scope.for_task(action_name=action.name))

    @classmethod
    def render_task_script(cls, script: str, task_scope: EvaluationScope) -> TaskOutcome:
        """render_task's own engine, on a bare script and an already
        task-view scope — also what an ActionTask runs, later and
        possibly in another process, against a rehydrated scope (see
        tracking/actuators/action_task.py): the same code path whether
        the task fires now or was deferred."""
        action_name = task_scope.action_name
        statements = TriggerExpressionAnalyzer.task_statements(script)
        snippets = []
        failures: list[tuple[str, Exception]] = []
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
                failures.append((statement, exc))
                continue
            if target is not None:
                task_scope[target] = result
            elif isinstance(result, JsSnippet):
                snippets.append(result)
        return TaskOutcome("\n".join(snippets) if snippets else None, tuple(failures))

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
