from __future__ import annotations

from typing import TYPE_CHECKING

from db import Db
from job import JobService
from notification.notification_service import NotificationService

from .actuator_set import ActuatorSet, FakeActuatorSet, LiveActuatorSet, OnEnterDispatcher
from .on_enter_task import ACTUATORS_FAKE, ACTUATORS_LIVE, OnEnterTask, ScopeHydrator

if TYPE_CHECKING:
    from ai import AiService
    from chat.ws_adapter import WsAdapter
    from project.project_service import ProjectService
    from whatsapp.whatsapp_service import WhatsAppService


class ActuatorSetFactory:
    """Registers the on-enter task type with the JobService at
    construction — before the service is started (main.py starts it
    last), so a hibernated row can never be claimed with nobody to
    hydrate it. The websocket adapter is the one late binding left
    (WsAdapter needs ChatService, which needs this factory): a task
    reads it through the hydrator at run time, and no task runs before
    main.py has bound it and started the JobService. `ai_service` is
    what a rehydrated actuator.prompt runs against."""

    def __init__(
        self, notification_service: NotificationService, db: Db, job_service: JobService,
        project_service: "ProjectService", ai_service: "AiService | None" = None,
    ) -> None:
        self._notification_service = notification_service
        self._db = db
        self._job_service = job_service
        self._enabled_test_sessions: set[int] = set()
        self._ws_adapter: "WsAdapter | None" = None
        self._whatsapp_service: "WhatsAppService | None" = None
        self._hydrator = ScopeHydrator(db, project_service, self, ai_service)
        job_service.register_task_type(OnEnterTask.TYPE, self._hydrator.hydrate)

    def set_ws_adapter(self, ws_adapter: "WsAdapter") -> None:
        self._ws_adapter = ws_adapter

    @property
    def ws_adapter(self) -> "WsAdapter | None":
        return self._ws_adapter

    def set_whatsapp_service(self, whatsapp_service: "WhatsAppService | None") -> None:
        self._whatsapp_service = whatsapp_service

    def _dispatcher(self, project_id: str, actuators: str) -> OnEnterDispatcher:
        return OnEnterDispatcher(self._job_service, self._hydrator, project_id=project_id, actuators=actuators)

    def live(self, *, project_id: str) -> LiveActuatorSet:
        """Bound to `project_id`: what its on-enter tasks are hibernated under."""
        return LiveActuatorSet(
            self._notification_service, self._dispatcher(project_id, ACTUATORS_LIVE),
            whatsapp_service=self._whatsapp_service,
        )

    def fake(self, *, project_id: str) -> FakeActuatorSet:
        """Same binding, real side effects suppressed (a test session
        with "Run actuators" off, or a project-wide test reset with no
        session at all) — its on-enter still runs as a task, so its
        notify()/celebrate() reach the browser the same way."""
        return FakeActuatorSet(self._dispatcher(project_id, ACTUATORS_FAKE))

    def for_session(self, session_id: int) -> ActuatorSet:
        session = self._db.get_chat_session(session_id)
        if session is None:
            raise FileNotFoundError(f"Session {session_id} does not exist.")
        if session["type"] == "test" and not self.is_enabled_for_test_session(session_id):
            return self.fake(project_id=session["project_id"])
        return self.live(project_id=session["project_id"])

    def is_enabled_for_test_session(self, session_id: int) -> bool:
        return session_id in self._enabled_test_sessions

    def set_enabled_for_test_session(self, session_id: int, enabled: bool) -> None:
        if enabled:
            self._enabled_test_sessions.add(session_id)
        else:
            self._enabled_test_sessions.discard(session_id)
