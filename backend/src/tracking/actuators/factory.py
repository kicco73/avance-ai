from __future__ import annotations

from db import Db
from jobs import AbstractJobQueue, ScheduledJobQueue
from notification.notification_service import NotificationService

from .actuator_set import ActuatorSet, FakeActuatorSet, LiveActuatorSet


class ActuatorSetFactory:

    def __init__(self, notification_service: NotificationService, db: Db, job_queue: AbstractJobQueue) -> None:
        self._notification_service = notification_service
        self._db = db
        self._enabled_test_sessions: set[int] = set()
        self._scheduled_job_queue = ScheduledJobQueue(job_queue)

    def live(self) -> LiveActuatorSet:
        return LiveActuatorSet(self._notification_service, self._scheduled_job_queue)

    def for_session(self, session_id: int) -> ActuatorSet:
        session = self._db.get_chat_session(session_id)
        is_test = session is not None and session["type"] == "test"
        if not is_test or self.is_enabled_for_test_session(session_id):
            return self.live()
        return FakeActuatorSet()

    def is_enabled_for_test_session(self, session_id: int) -> bool:
        return session_id in self._enabled_test_sessions

    def set_enabled_for_test_session(self, session_id: int, enabled: bool) -> None:
        if enabled:
            self._enabled_test_sessions.add(session_id)
        else:
            self._enabled_test_sessions.discard(session_id)
