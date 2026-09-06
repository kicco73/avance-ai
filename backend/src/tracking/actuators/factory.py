from __future__ import annotations

from typing import TYPE_CHECKING

from db import Db
from job import JobService
from notification.notification_service import NotificationService

from .actuator_set import ActuatorSet, FakeActuatorSet, LiveActuatorSet, OnEnterDispatcher
from .on_enter_task import ACTUATORS_FAKE, ACTUATORS_LIVE, OnEnterTask, ScopeHydrator

if TYPE_CHECKING:
    from ai import AiService
    from chat.ws_notifications import WsNotifications
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
        # session_id -> the username actuator.switch_to_human(user_id)
        # last targeted for it (see actuator_set.py) — cleared by
        # switch_to_ai. Same "no restart survives this" caveat as
        # _enabled_test_sessions above; read by TrackingService._process
        # to decide who answers a session's next turn.
        self._human_operators: dict[int, str] = {}
        self._ws_notifications: "WsNotifications | None" = None
        self._whatsapp_service: "WhatsAppService | None" = None
        self._hydrator = ScopeHydrator(db, project_service, self, ai_service)
        job_service.register_task_type(OnEnterTask.TYPE, self._hydrator.hydrate)

    def get_human_operator(self, session_id: int) -> str | None:
        return self._human_operators.get(session_id)

    def set_human_operator(self, session_id: int, user_id: str) -> None:
        self._human_operators[session_id] = user_id

    def clear_human_operator(self, session_id: int) -> None:
        self._human_operators.pop(session_id, None)

    def set_ws_notifications(self, ws_notifications: "WsNotifications") -> None:
        self._ws_notifications = ws_notifications

    @property
    def ws_notifications(self) -> "WsNotifications | None":
        return self._ws_notifications

    def set_whatsapp_service(self, whatsapp_service: "WhatsAppService | None") -> None:
        self._whatsapp_service = whatsapp_service

    def _dispatcher(self, project_id: str, actuators: str) -> OnEnterDispatcher:
        return OnEnterDispatcher(self._job_service, self._hydrator, project_id=project_id, actuators=actuators)

    def live(self, *, project_id: str) -> LiveActuatorSet:
        """Bound to `project_id`: what its on-enter tasks are hibernated under."""
        return LiveActuatorSet(
            self._notification_service, self._dispatcher(project_id, ACTUATORS_LIVE),
            whatsapp_service=self._whatsapp_service, factory=self,
        )

    def fake(self, *, project_id: str) -> FakeActuatorSet:
        """Same binding, real side effects suppressed (a test session
        with "Run actuators" off, or a project-wide test reset with no
        session at all) — its on-enter still runs as a task, so its
        notify()/celebrate() reach the browser the same way."""
        return FakeActuatorSet(self._dispatcher(project_id, ACTUATORS_FAKE), factory=self)

    def for_session(self, session_id: int) -> ActuatorSet:
        session = self._db.get_chat_session(session_id)
        if session is None:
            raise FileNotFoundError(f"Session {session_id} does not exist.")
        if session["type"] in ("test", "preview") and not self.is_enabled_for_test_session(session_id):
            actuator_set = self.fake(project_id=session["project_id"])
        else:
            actuator_set = self.live(project_id=session["project_id"])
        return actuator_set.with_session(session_id)

    def is_enabled_for_test_session(self, session_id: int) -> bool:
        return session_id in self._enabled_test_sessions

    def set_enabled_for_test_session(self, session_id: int, enabled: bool) -> None:
        if enabled:
            self._enabled_test_sessions.add(session_id)
        else:
            self._enabled_test_sessions.discard(session_id)
