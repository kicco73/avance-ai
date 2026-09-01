from __future__ import annotations

from abc import ABC, abstractmethod

from logging_factory import LoggerFactory
from notification.notification_service import NotificationService

logger = LoggerFactory.get_logger(__name__)

_SEND_MAIL_SUBJECT = "Notification from Avance"


class ActuatorSet(ABC):

    @abstractmethod
    def send_mail(self, to: str, body_md: str) -> None:
        raise NotImplementedError


class LiveActuatorSet(ActuatorSet):

    def __init__(self, notification_service: NotificationService) -> None:
        self._notification_service = notification_service

    def send_mail(self, to: str, body_md: str) -> None:
        self._notification_service.enqueue_mail(to, _SEND_MAIL_SUBJECT, body_md)


class FakeActuatorSet(ActuatorSet):

    def __init__(self) -> None:
        self.notices: list[str] = []

    def send_mail(self, to: str, body_md: str) -> None:
        message = f"send_mail(to={to!r}) — Run actuators is off, no email was sent."
        self.notices.append(message)
        logger.info(message)
