from __future__ import annotations

import asyncio
import copy
import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING, TypeVar

from system import bus
from automaton.automaton import Action, DeferredExpression, JsSnippet
from automaton.project_services import ProjectServices
from automaton.scope import EvaluationScope
from system.bus import MAIL_SEND, OUTPUT_TEXT, Message
from turn.channels import WHATSAPP_CHAT
from system.logging_factory import LoggerFactory
from scheduler import SchedulerService
from system.session import Session

from .action_task import ActionTask, ScopeHydrator

if TYPE_CHECKING:
    from ai import AiService
    from tracking.actuators.factory import TaskNamespaceFactory
    from tracking.sources import ToolSet


logger = LoggerFactory.get_logger(__name__)

_SEND_MAIL_SUBJECT = "Notification from Avance"


class _NoMailService:
    """What a mail.send coming back undelivered means: either this build
    has no mail-service, or the project declared `mail: disabled` — both
    say the same thing to a task that expected a mail to go out (see
    mail.skill.required_by and automaton/project_services.py)."""

    async def bounced(self, message: Message) -> None:
        raise ValueError("No mail-service available to this project — task.send_mail can't run.")

_T = TypeVar("_T")


def _run_sync(coro: Coroutine[Any, Any, _T]) -> _T:
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


class TaskNamespace(ABC):
    """`prompt` compiles to a read-only generation call — no real-world
    side effect either subclass could meaningfully suppress, so it
    behaves identically regardless of a test session's "Run actuators"
    toggle. Only `send_mail`/`whatsapp`/`defer` (real side effects) are
    each subclass's own concern. `celebrate`/`notify`/`show`/
    `switch_to_human`/`switch_to_ai` used to live here too — they moved
    to ChatNamespace (see chat_namespace.py), since only an on-exit
    script may call them now."""

    def __init__(
        self, dispatcher: "TaskDispatcher | None" = None, factory: "TaskNamespaceFactory | None" = None,
    ) -> None:
        # Bound fresh per task evaluation via with_ai_service —
        # never set any other way (see EvaluationScopeBuilder.build).
        self._ai_service: "AiService | None" = None
        # Same lifecycle as _ai_service above — the tool catalog of
        # whichever state this task is actually evaluated for (see
        # EvaluationScopeBuilder.build's own with_ai_service call), used
        # only by prompt() below. None wherever _ai_service is too, or
        # for a state with neither ai-may-read-sources nor
        # ai-must-read-sources declared.
        self._tool_set: "ToolSet | None" = None
        # How this namespace gets a task script run as a Task. None only
        # for a bare namespace nobody wired to a SchedulerService (a test
        # replay's own FakeTaskNamespace default): the script then runs
        # inline and its output is dropped, since no browser is listening anyway.
        self._dispatcher = dispatcher
        # The factory that built this namespace — shared with
        # ChatNamespace for the human-operator bookkeeping (see
        # TaskNamespaceFactory.set_human_operator/clear_human_operator),
        # same "ask the thing that made you" shape as _dispatcher above.
        self._factory = factory
        # Bound fresh per task evaluation via with_session — never
        # set any other way. None for a namespace built without a firing
        # session (e.g. a deferred call, or a project-wide test reset).
        self._session_id: int | None = None
        # What the project running this task declared about each service
        # it may reach (project.services — see
        # automaton/project_services.py). Bound per evaluation from the
        # scope's own automaton, like _ai_service above; the empty one
        # answers "optional" for everything, which is what a namespace
        # nobody bound to a project has always done.
        self._services = ProjectServices()

    def schedule_task(self, action: Action, scope: EvaluationScope, *, session_id: int | None) -> None:
        """Runs `action.task` as an ActionTask due now (see
        action_task.py) — never inline in the request that fired it.
        `scope`: the full scope the transition was evaluated in; the
        task keeps its own task view."""
        if not action.task:
            return
        if self._dispatcher is None:
            # XXX Compiled automaton requirement - do not touch.
            # XXX Dispatched on the scope's own automaton, not on the
            # Automaton class: a compiled automaton runs a task without
            # interpreting `action.task` text, and a hardcoded class name
            # would bypass its override entirely.
            scope.automaton.render_task(action, scope)
            return
        self._dispatcher.schedule_now(action, scope, session_id=session_id)

    def prompt(self, prompt: str) -> str:
        """Runs `prompt` as one extra, synchronous, fully isolated
        generation call — no history, no attachments, no signal/env
        context, nothing persisted — and returns its text. Returns ""
        (logged) wherever no AI service is bound, e.g. a project-wide
        test reset with no real session behind it."""
        if self._ai_service is None:
            logger.warning("task.prompt() called with no AI service bound — returning ''.")
            return ""
        # tool_set only actually passed when bound — a fake AiService
        # predating tool-calling (most existing tests' own doubles, see
        # tests/conftest.py) declares no such parameter at all.
        kwargs = {"tool_set": self._tool_set} if self._tool_set is not None else {}
        return _run_sync(self._ai_service.prompt(prompt, **kwargs))

    def with_ai_service(self, ai_service: "AiService", tool_set: "ToolSet | None" = None) -> "TaskNamespace":
        """A copy of this namespace bound to `ai_service` (and,
        optionally, the tool catalog of whichever state this task is
        being evaluated for — see EvaluationScopeBuilder.build) — never
        mutates `self`, so the long-lived instance a factory hands out
        stays reusable across calls."""
        bound = copy.copy(self)
        bound._ai_service = ai_service
        bound._tool_set = tool_set
        return bound

    def with_services(self, services: ProjectServices) -> "TaskNamespace":
        """A copy bound to what the project being evaluated declared
        about each service — same never-mutate-self shape as
        with_ai_service above (see EvaluationScopeBuilder.build)."""
        bound = copy.copy(self)
        bound._services = services
        return bound

    def with_session(self, session_id: int) -> "TaskNamespace":
        """A copy of this namespace bound to the session whose
        task is actually running — see ScopeHydrator.build_scope,
        the only place session_id is known at the moment a script's
        task.* calls are evaluated. Same never-mutate-self shape as
        with_ai_service above."""
        bound = copy.copy(self)
        bound._session_id = session_id
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


class TaskDispatcher(object):
    """What turns a task (now) or a deferred lambda (later) into an
    ActionTask on the SchedulerService, under (the current user, one project)
    — the two things a Task row keys on (see scheduler/task.py) — and marked
    with which task namespace (live or fake) must be rebuilt to run it."""

    def __init__(
        self, scheduler_service: SchedulerService, hydrator: ScopeHydrator, *, project_id: str, namespace_kind: str,
    ) -> None:
        self._scheduler_service = scheduler_service
        self._hydrator = hydrator
        self._project_id = project_id
        self._namespace_kind = namespace_kind

    @property
    def project_id(self) -> str:
        return self._project_id

    def _check_project(self, scope: EvaluationScope) -> None:
        if scope.automaton.project_id != self._project_id:
            raise ValueError(
                f"task evaluated for project '{scope.automaton.project_id}' but this task namespace "
                f"belongs to '{self._project_id}'."
            )

    def schedule_now(self, action: Action, scope: EvaluationScope, *, session_id: int | None) -> None:
        self._check_project(scope)
        task = ActionTask.now(
            action, scope, username=Session().user, namespace_kind=self._namespace_kind, session_id=session_id,
            hydrator=self._hydrator,
        )
        self._scheduler_service.schedule(task, datetime.now(timezone.utc))

    def schedule_later(self, act: DeferredExpression, when: datetime) -> None:
        self._check_project(act.scope)
        task = ActionTask.later(
            act, when, username=Session().user, namespace_kind=self._namespace_kind, hydrator=self._hydrator,
        )
        self._scheduler_service.schedule(task, when)


class LiveTaskNamespace(TaskNamespace):
    """Always bound to one project through its dispatcher: every
    task and every defer() is hibernated under (the current user,
    that project) — never a session, which will be over by the time a
    deferred call runs (see action_task.py)."""

    def __init__(
        self, dispatcher: "TaskDispatcher", factory: "TaskNamespaceFactory | None" = None,
    ) -> None:
        super().__init__(dispatcher, factory)

    def send_mail(self, to: str, body_md: str) -> JsSnippet | None:
        _run_sync(self._services["mail"].deliver(Message(
            type=MAIL_SEND, username=Session().user,
            body={"to": to, "subject": _SEND_MAIL_SUBJECT, "body_md": body_md},
        ), _NoMailService()))
        return None

    def whatsapp(self, phone_number: str, message_md: str) -> bool:
        """The text goes out on the channel it names, and the posting is
        the answer: False means nothing in this build carries it — or
        that this project declared `whatsapp: disabled`, which reaches a
        caller as the same "nobody carried it" an unconfigured channel
        always meant here."""
        return _run_sync(self._services["whatsapp"].publish(Message(
            type=OUTPUT_TEXT, body=message_md, username=phone_number.strip().lstrip("+"),
            channel=WHATSAPP_CHAT, project_id=self._dispatcher.project_id,
        )))

    def defer(self, act: Callable[[], None], when: datetime) -> JsSnippet | None:
        # Both refusals are unreachable from a built index.yml — the
        # AutomatonBuilder already requires a zero-argument lambda and a
        # datetime-shaped `when` (see TriggerExpressionAnalyzer.defer_violations);
        # they guard the Python-level API only.
        if not isinstance(act, DeferredExpression):
            raise TypeError(
                f"task.defer needs a `lambda: ...` evaluated from a task line, got {type(act).__name__}."
            )
        if not isinstance(when, datetime):
            raise TypeError(f"task.defer needs a datetime as `when`, got {type(when).__name__}.")
        self._dispatcher.schedule_later(act, when)
        return None


class FakeTaskNamespace(TaskNamespace):
    """Stands in for LiveTaskNamespace while a test session's own "Run
    actuators" toggle is off — a real side effect is suppressed and
    reported to the frontend via `notify(...)` instead of actually
    happening (see PROJECT_SPECS.md §6.5). `_notify` builds that same
    wire-ready JS text `chat.notify(...)` would, but locally: `notify`
    itself is a ChatNamespace method now, not reachable from a `task:` script."""

    def _notify(self, title: str, message: str) -> JsSnippet | None:
        return JsSnippet(f"notify({json.dumps(title)}, {json.dumps(message)})")

    def send_mail(self, to: str, body_md: str) -> JsSnippet | None:
        message = f"send_mail(to={to!r}) — Run actuators is off, no email was sent."
        logger.info(message)
        return self._notify("Task (test)", message)

    def whatsapp(self, phone_number: str, message_md: str) -> JsSnippet | None:
        message = f"whatsapp(to={phone_number!r}) — Run actuators is off, no message was sent."
        logger.info(message)
        return self._notify("Task (test)", message)

    def defer(self, act: Callable[[], None], when: datetime) -> JsSnippet | None:
        message = f"defer(when={when.isoformat()!r}) — Run actuators is off, nothing was scheduled."
        logger.info(message)
        return self._notify("Task (test)", message)
