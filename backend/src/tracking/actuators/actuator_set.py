from __future__ import annotations

import copy
import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from jobs import Scheduler
from logging_factory import LoggerFactory
from notification.notification_service import NotificationService
from session import Session

from .deferred_job import DeferredActuatorJob

if TYPE_CHECKING:
    from chat.ws_adapter import WsAdapter

    from .prompt_context import PromptContext

logger = LoggerFactory.get_logger(__name__)

_SEND_MAIL_SUBJECT = "Notification from Avance"


class ActuatorSet(ABC):
    """`celebrate`/`notify`/`prompt` compile straight to the frontend's
    own onEnterActions.js locals of the same name (see
    Automaton.render_on_enter) or, for `prompt`, run a read-only
    generation call — no real-world side effect either subclass could
    meaningfully suppress, so all three behave identically regardless of
    a test session's "Run actuators" toggle. Only `send_mail`/`defer`
    (real side effects) are each subclass's own concern."""

    def __init__(self) -> None:
        # Bound fresh per on-enter evaluation via with_prompt_context —
        # never set any other way (see EvaluationScopeBuilder.build).
        self._prompt_context: "PromptContext | None" = None

    def celebrate(self) -> str | None:
        return "celebrate()"

    def notify(self, title: str, body_md: str) -> str | None:
        return f"notify({json.dumps(title)}, {json.dumps(body_md)})"

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
    def send_mail(self, to: str, body_md: str) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def defer(self, act: Callable[[], None], when: datetime) -> str | None:
        raise NotImplementedError


class LiveActuatorSet(ActuatorSet):

    def __init__(
        self, notification_service: NotificationService, scheduled_job_queue: Scheduler,
        ws_adapter: "WsAdapter | None",
    ) -> None:
        super().__init__()
        self._notification_service = notification_service
        self._scheduled_job_queue = scheduled_job_queue
        self._ws_adapter = ws_adapter

    def send_mail(self, to: str, body_md: str) -> str | None:
        # Raises (NotificationError) if this deployment's own .config.yml
        # declares no notification-service section — see
        # NotificationService's own docstring on why that's deferred to
        # here rather than failing at app boot.
        self._notification_service.enqueue_mail(to, _SEND_MAIL_SUBJECT, body_md)
        return None

    def defer(self, act: Callable[[], None], when: datetime) -> str | None:
        job = DeferredActuatorJob(act, Session().user, self._ws_adapter)
        self._scheduled_job_queue.submit(job, timestamp=when)
        return None


class FakeActuatorSet(ActuatorSet):
    """Stands in for LiveActuatorSet while a test session's own "Run
    actuators" toggle is off — a real side effect is suppressed and
    reported to the frontend via `notify(...)` instead of actually
    happening (see PROJECT_SPECS.md §6.5)."""

    def send_mail(self, to: str, body_md: str) -> str | None:
        message = f"send_mail(to={to!r}) — Run actuators is off, no email was sent."
        logger.info(message)
        return self.notify("Actuator (test)", message)

    def defer(self, act: Callable[[], None], when: datetime) -> str | None:
        message = f"defer(when={when.isoformat()!r}) — Run actuators is off, nothing was scheduled."
        logger.info(message)
        return self.notify("Actuator (test)", message)
