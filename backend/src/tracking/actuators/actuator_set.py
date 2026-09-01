from __future__ import annotations

import json
from abc import ABC, abstractmethod

from logging_factory import LoggerFactory
from notification.notification_service import NotificationService

logger = LoggerFactory.get_logger(__name__)

_SEND_MAIL_SUBJECT = "Notification from Avance"


class ActuatorSet(ABC):
    """`celebrate`/`notify` compile straight to the frontend's own
    onEnterActions.js locals of the same name (see Automaton.render_on_enter)
    — a pure client-visual effect with no real-world side effect either
    subclass could meaningfully suppress, so both behave identically
    regardless of a test session's "Run actuators" toggle. Only
    `send_mail` (a real side effect) is each subclass's own concern."""

    def celebrate(self) -> str | None:
        return "celebrate()"

    def notify(self, title: str, body_md: str) -> str | None:
        return f"notify({json.dumps(title)}, {json.dumps(body_md)})"

    @abstractmethod
    def send_mail(self, to: str, body_md: str) -> str | None:
        raise NotImplementedError


class LiveActuatorSet(ActuatorSet):

    def __init__(self, notification_service: NotificationService) -> None:
        self._notification_service = notification_service

    def send_mail(self, to: str, body_md: str) -> str | None:
        self._notification_service.enqueue_mail(to, _SEND_MAIL_SUBJECT, body_md)
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
