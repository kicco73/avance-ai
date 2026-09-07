from __future__ import annotations

from typing import TYPE_CHECKING

from db import Db
from job import JobService
from notification.notification_service import NotificationService

from .actuator_set import FakeTaskNamespace, LiveTaskNamespace, TaskDispatcher, TaskNamespace
from .action_task import TASK_NAMESPACE_FAKE, TASK_NAMESPACE_LIVE, ActionTask, ScopeHydrator
from .chat_namespace import ChatNamespace, FakeChatNamespace, LiveChatNamespace

if TYPE_CHECKING:
    from ai import AiService
    from chat.ws_notifications import WsNotifications
    from project.project_service import ProjectService
    from whatsapp.whatsapp_service import WhatsAppService


class TaskNamespaceFactory:
    """Registers the task type with the JobService at
    construction — before the service is started (main.py starts it
    last), so a hibernated row can never be claimed with nobody to
    hydrate it. The websocket adapter is the one late binding left
    (WsAdapter needs ChatService, which needs this factory): a task
    reads it through the hydrator at run time, and no task runs before
    main.py has bound it and started the JobService. `ai_service` is
    what a rehydrated task.prompt runs against. Builds both a task
    namespace (`.live`/`.fake`/`.for_session`) and a chat namespace
    (`.chat_live`/`.chat_fake`/`.chat_for_session`) — the two share the
    exact same human-operator bookkeeping and ws_notifications adapter
    below, so one factory holds both rather than duplicating that state
    into a second class."""

    def __init__(
        self, notification_service: NotificationService, db: Db, job_service: JobService,
        project_service: "ProjectService", ai_service: "AiService | None" = None,
    ) -> None:
        self._notification_service = notification_service
        self._db = db
        self._job_service = job_service
        self._enabled_test_sessions: set[int] = set()
        # session_id -> the username chat.switch_to_human(user_id)
        # last targeted for it (see chat_namespace.py) — cleared by
        # switch_to_ai. Same "no restart survives this" caveat as
        # _enabled_test_sessions above; read by TrackingService._process
        # to decide who answers a session's next turn.
        self._human_operators: dict[int, str] = {}
        self._ws_notifications: "WsNotifications | None" = None
        self._whatsapp_service: "WhatsAppService | None" = None
        self._hydrator = ScopeHydrator(db, project_service, self, ai_service)
        job_service.register_task_type(ActionTask.TYPE, self._hydrator.hydrate)

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

    def _dispatcher(self, project_id: str, namespace_kind: str) -> TaskDispatcher:
        return TaskDispatcher(self._job_service, self._hydrator, project_id=project_id, namespace_kind=namespace_kind)

    # --- task namespace ---------------------------------------------------

    def live(self, *, project_id: str) -> LiveTaskNamespace:
        """Bound to `project_id`: what its task tasks are hibernated under."""
        return LiveTaskNamespace(
            self._notification_service, self._dispatcher(project_id, TASK_NAMESPACE_LIVE),
            whatsapp_service=self._whatsapp_service, factory=self,
        )

    def fake(self, *, project_id: str) -> FakeTaskNamespace:
        """Same binding, real side effects suppressed (a test session
        with "Run actuators" off, or a project-wide test reset with no
        session at all) — its task still runs as a task, so whatever it
        reports still reaches the browser the same way."""
        return FakeTaskNamespace(self._dispatcher(project_id, TASK_NAMESPACE_FAKE), factory=self)

    def for_session(self, session_id: int) -> TaskNamespace:
        session = self._db.get_chat_session(session_id)
        if session is None:
            raise FileNotFoundError(f"Session {session_id} does not exist.")
        if session["type"] in ("test", "preview") and not self.is_enabled_for_test_session(session_id):
            task_namespace = self.fake(project_id=session["project_id"])
        else:
            task_namespace = self.live(project_id=session["project_id"])
        return task_namespace.with_session(session_id)

    # --- chat namespace -----------------------------------------------------

    def chat_live(self, *, project_id: str) -> LiveChatNamespace:
        return LiveChatNamespace(project_id, factory=self)

    def chat_fake(self, *, project_id: str) -> FakeChatNamespace:
        return FakeChatNamespace(factory=self)

    def chat_for_session(self, session_id: int) -> ChatNamespace:
        session = self._db.get_chat_session(session_id)
        if session is None:
            raise FileNotFoundError(f"Session {session_id} does not exist.")
        if session["type"] in ("test", "preview") and not self.is_enabled_for_test_session(session_id):
            chat_namespace = self.chat_fake(project_id=session["project_id"])
        else:
            chat_namespace = self.chat_live(project_id=session["project_id"])
        return chat_namespace.with_session(session_id)

    # --- shared -------------------------------------------------------------

    def is_enabled_for_test_session(self, session_id: int) -> bool:
        return session_id in self._enabled_test_sessions

    def set_enabled_for_test_session(self, session_id: int, enabled: bool) -> None:
        if enabled:
            self._enabled_test_sessions.add(session_id)
        else:
            self._enabled_test_sessions.discard(session_id)
