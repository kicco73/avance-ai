from __future__ import annotations

import asyncio
import copy
import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING, TypeVar

from automaton.automaton import Action, Automaton, DeferredExpression, JsSnippet
from automaton.scope import EvaluationScope
from job import JobService
from logging_factory import LoggerFactory
from notification.notification_service import NotificationService
from session import Session

from .on_enter_task import OnEnterTask, ScopeHydrator

if TYPE_CHECKING:
    from whatsapp.whatsapp_service import WhatsAppService

    from ai import AiService
    from tracking.sources import ToolSet


logger = LoggerFactory.get_logger(__name__)

_SEND_MAIL_SUBJECT = "Notification from Avance"

_T = TypeVar("_T")


def _run_sync(coro: Coroutine[Any, Any, _T]) -> _T:
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


class ActuatorSet(ABC):
    """`celebrate`/`notify`/`show`/`prompt` compile straight to the frontend's
    own onEnterActions.js locals of the same name (see
    Automaton.render_on_enter) or, for `prompt`, run a read-only
    generation call — no real-world side effect either subclass could
    meaningfully suppress, so all four behave identically regardless of
    a test session's "Run actuators" toggle. Only `send_mail`/`defer`
    (real side effects) are each subclass's own concern."""

    def __init__(self, dispatcher: "OnEnterDispatcher | None" = None) -> None:
        # Bound fresh per on-enter evaluation via with_ai_service —
        # never set any other way (see EvaluationScopeBuilder.build).
        self._ai_service: "AiService | None" = None
        # Same lifecycle as _ai_service above — the tool catalog of
        # whichever state this on-enter is actually evaluated for (see
        # EvaluationScopeBuilder.build's own with_ai_service call), used
        # only by prompt() below. None wherever _ai_service is too, or
        # for a state with neither ai-may-query-sources nor
        # ai-must-query-sources declared.
        self._tool_set: "ToolSet | None" = None
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
        """Runs `prompt` as one extra, synchronous, fully isolated
        generation call — no history, no attachments, no signal/env
        context, nothing persisted — and returns its text. Returns ""
        (logged) wherever no AI service is bound, e.g. a project-wide
        test reset with no real session behind it."""
        if self._ai_service is None:
            logger.warning("actuator.prompt() called with no AI service bound — returning ''.")
            return ""
        # tool_set only actually passed when bound — a fake AiService
        # predating tool-calling (most existing tests' own doubles, see
        # tests/conftest.py) declares no such parameter at all.
        kwargs = {"tool_set": self._tool_set} if self._tool_set is not None else {}
        return _run_sync(self._ai_service.prompt(prompt, **kwargs))

    def with_ai_service(self, ai_service: "AiService", tool_set: "ToolSet | None" = None) -> "ActuatorSet":
        """A copy of this actuator set bound to `ai_service` (and,
        optionally, the tool catalog of whichever state this on-enter is
        being evaluated for — see EvaluationScopeBuilder.build) — never
        mutates `self`, so the long-lived instance a factory hands out
        stays reusable across calls."""
        bound = copy.copy(self)
        bound._ai_service = ai_service
        bound._tool_set = tool_set
        return bound

    @abstractmethod
    def send_mail(self, to: str, body_md: str) -> JsSnippet | None:
        raise NotImplementedError

    @abstractmethod
    def whatsapp(self, phone_number: str, message_md: str) -> JsSnippet | bool | None:
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

    def __init__(
        self, notification_service: NotificationService, dispatcher: "OnEnterDispatcher",
        whatsapp_service: "WhatsAppService | None" = None,
    ) -> None:
        super().__init__(dispatcher)
        self._notification_service = notification_service
        self._whatsapp_service = whatsapp_service

    def send_mail(self, to: str, body_md: str) -> JsSnippet | None:
        # Raises (NotificationError) if this deployment's own .config.yml
        # declares no notification-service section — see
        # NotificationService's own docstring on why that's deferred to
        # here rather than failing at app boot.
        self._notification_service.enqueue_mail(to, _SEND_MAIL_SUBJECT, body_md)
        return None

    def whatsapp(self, phone_number: str, message_md: str) -> bool:
        if self._whatsapp_service is None:
            logger.warning("actuator.whatsapp() called but no 'whatsapp-service' section in .config.yml — message not sent.")
            return False
        return _run_sync(self._whatsapp_service.send_message(phone_number, message_md, self._dispatcher.project_id))

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

    def whatsapp(self, phone_number: str, message_md: str) -> JsSnippet | None:
        message = f"whatsapp(to={phone_number!r}) — Run actuators is off, no message was sent."
        logger.info(message)
        return self.notify("Actuator (test)", message)

    def defer(self, act: Callable[[], None], when: datetime) -> JsSnippet | None:
        message = f"defer(when={when.isoformat()!r}) — Run actuators is off, nothing was scheduled."
        logger.info(message)
        return self.notify("Actuator (test)", message)
