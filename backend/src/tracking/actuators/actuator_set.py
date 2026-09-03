from __future__ import annotations

import copy
import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from automaton.automaton import Action, Automaton, DeferredExpression, JsSnippet
from automaton.scope import EvaluationScope
from job import JobService
from logging_factory import LoggerFactory
from notification.notification_service import NotificationService
from session import Session

from .on_enter_task import OnEnterTask, ScopeHydrator

if TYPE_CHECKING:
    from .prompt_context import PromptContext

logger = LoggerFactory.get_logger(__name__)

_SEND_MAIL_SUBJECT = "Notification from Avance"


class ActuatorSet(ABC):
    """`celebrate`/`notify`/`show`/`prompt` compile straight to the frontend's
    own onEnterActions.js locals of the same name (see
    Automaton.render_on_enter) or, for `prompt`, run a read-only
    generation call — no real-world side effect either subclass could
    meaningfully suppress, so all four behave identically regardless of
    a test session's "Run actuators" toggle. Only `send_mail`/`defer`
    (real side effects) are each subclass's own concern."""

    def __init__(self, dispatcher: "OnEnterDispatcher | None" = None) -> None:
        # Bound fresh per on-enter evaluation via with_prompt_context —
        # never set any other way (see EvaluationScopeBuilder.build).
        self._prompt_context: "PromptContext | None" = None
        # How this set gets an on-enter script run as a Task. None only
        # for a bare set nobody wired to a JobService (a test replay's
        # own FakeActuatorSet default): the script then runs inline and
        # its output is dropped, since no browser is listening anyway.
        self._dispatcher = dispatcher

    def schedule_on_enter(self, action: Action, scope: EvaluationScope, *, session_id: int | None) -> None:
        """Runs `action.on_enter` as an OnEnterTask due now (see
        on_enter_task.py) — never inline in the request that fired it.
        `scope`: the full scope the transition was evaluated in; the
        task keeps its actuator view."""
        if not action.on_enter:
            return
        if self._dispatcher is None:
            Automaton.render_on_enter(action, scope)
            return
        self._dispatcher.schedule_now(action, scope, session_id=session_id)

    def celebrate(self) -> JsSnippet | None:
        return JsSnippet("celebrate()")

    def notify(self, title: str, body_md: str) -> JsSnippet | None:
        return JsSnippet(f"notify({json.dumps(title)}, {json.dumps(body_md)})")

    def show(self, body_md: str) -> JsSnippet | None:
        return JsSnippet(f"show({json.dumps(body_md)})")

    def prompt(self, prompt: str) -> str:
        """Runs `prompt` as one extra, synchronous, read-only generation
        call — general-prompt + attachments (global and the current
        state's own) + signal/env context, only the resulting text
        aggregated back — and returns its text. No message is persisted
        and no env/signal/audio channel is applied (see PromptContext).
        Returns "" (logged) wherever no session context is bound, e.g. a
        project-wide test reset with no real session behind it."""
        if self._prompt_context is None:
            logger.warning("actuator.prompt() called with no session context bound — returning ''.")
            return ""
        return self._prompt_context.run(prompt)

    def with_prompt_context(self, prompt_context: "PromptContext") -> "ActuatorSet":
        """A copy of this actuator set bound to `prompt_context` — never
        mutates `self`, so the long-lived instance a factory hands out
        stays reusable across calls with different automaton/state/session context."""
        bound = copy.copy(self)
        bound._prompt_context = prompt_context
        return bound

    @abstractmethod
    def send_mail(self, to: str, body_md: str) -> JsSnippet | None:
        raise NotImplementedError

    @abstractmethod
    def defer(self, act: Callable[[], None], when: datetime) -> JsSnippet | None:
        raise NotImplementedError


class OnEnterDispatcher(object):
    """What turns an on-enter (now) or a deferred lambda (later) into an
    OnEnterTask on the JobService, under (the current user, one project)
    — the two things a Task row keys on (see jobs/task.py) — and marked
    with which actuator set (live or fake) must be rebuilt to run it."""

    def __init__(self, job_service: JobService, hydrator: ScopeHydrator, *, project_id: str, actuators: str) -> None:
        self._job_service = job_service
        self._hydrator = hydrator
        self._project_id = project_id
        self._actuators = actuators

    @property
    def project_id(self) -> str:
        return self._project_id

    def _check_project(self, scope: EvaluationScope) -> None:
        if scope.automaton.project_id != self._project_id:
            raise ValueError(
                f"on-enter evaluated for project '{scope.automaton.project_id}' but this actuator set "
                f"belongs to '{self._project_id}'."
            )

    def schedule_now(self, action: Action, scope: EvaluationScope, *, session_id: int | None) -> None:
        self._check_project(scope)
        task = OnEnterTask.now(
            action, scope, username=Session().user, actuators=self._actuators, session_id=session_id,
            hydrator=self._hydrator,
        )
        self._job_service.schedule(task, datetime.now(timezone.utc))

    def schedule_later(self, act: DeferredExpression, when: datetime) -> None:
        self._check_project(act.scope)
        task = OnEnterTask.later(
            act, when, username=Session().user, actuators=self._actuators, hydrator=self._hydrator,
        )
        self._job_service.schedule(task, when)


class LiveActuatorSet(ActuatorSet):
    """Always bound to one project through its dispatcher: every
    on-enter and every defer() is hibernated under (the current user,
    that project) — never a session, which will be over by the time a
    deferred call runs (see on_enter_task.py)."""

    def __init__(self, notification_service: NotificationService, dispatcher: "OnEnterDispatcher") -> None:
        super().__init__(dispatcher)
        self._notification_service = notification_service

    def send_mail(self, to: str, body_md: str) -> JsSnippet | None:
        # Raises (NotificationError) if this deployment's own .config.yml
        # declares no notification-service section — see
        # NotificationService's own docstring on why that's deferred to
        # here rather than failing at app boot.
        self._notification_service.enqueue_mail(to, _SEND_MAIL_SUBJECT, body_md)
        return None

    def defer(self, act: Callable[[], None], when: datetime) -> JsSnippet | None:
        # Both refusals are unreachable from a built index.yml — the
        # AutomatonBuilder already requires a zero-argument lambda and a
        # datetime-shaped `when` (see TriggerExpressionAnalyzer.defer_violations);
        # they guard the Python-level API only.
        if not isinstance(act, DeferredExpression):
            raise TypeError(
                f"actuator.defer needs a `lambda: ...` evaluated from an on-enter line, got {type(act).__name__}."
            )
        if not isinstance(when, datetime):
            raise TypeError(f"actuator.defer needs a datetime as `when`, got {type(when).__name__}.")
        self._dispatcher.schedule_later(act, when)
        return None


class FakeActuatorSet(ActuatorSet):
    """Stands in for LiveActuatorSet while a test session's own "Run
    actuators" toggle is off — a real side effect is suppressed and
    reported to the frontend via `notify(...)` instead of actually
    happening (see PROJECT_SPECS.md §6.5)."""

    def send_mail(self, to: str, body_md: str) -> JsSnippet | None:
        message = f"send_mail(to={to!r}) — Run actuators is off, no email was sent."
        logger.info(message)
        return self.notify("Actuator (test)", message)

    def defer(self, act: Callable[[], None], when: datetime) -> JsSnippet | None:
        message = f"defer(when={when.isoformat()!r}) — Run actuators is off, nothing was scheduled."
        logger.info(message)
        return self.notify("Actuator (test)", message)
