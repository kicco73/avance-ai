"""Admin-facing side effect of a published revision's build health
flipping broken<->healthy (see ProjectManager.recompute_availability,
which is the only publisher of ProjectPublishedHealthChanged) — one log
line, one SystemWarning row per admin, one best-effort ws push to every
connected admin. Split into a job (same "sync event -> async work" bridge
WakeupService uses for its own ws push) since publish() itself is
synchronous and may run from a plain (threadpool-executed) route handler,
where there is no running event loop to push over a websocket from directly."""
from __future__ import annotations

from auth.roles import role_satisfies
from chat.ws_notifications import WsNotifications
from db import Db
from events import ProjectPublishedHealthChanged, subscribe
from job import JobService
from jobs.job import CancelableJob
from logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)


class ProjectHealthNotificationJob(CancelableJob):
    def __init__(self, db: Db, ws_notifications: WsNotifications, project_id: str, revision: int, error: str | None) -> None:
        super().__init__(key=f"project-health:{project_id}:{revision}:{error is not None}", username="system")
        self._db = db
        self._ws_notifications = ws_notifications
        self._project_id = project_id
        self._revision = revision
        self._error = error

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        return 1, ()

    @property
    def is_background(self) -> bool:
        return True

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        if self._error is None:
            logger.info("Project '%s' revision %s builds again.", self._project_id, self._revision)
            return
        logger.error(
            "Project '%s' revision %s no longer builds — %s", self._project_id, self._revision, self._error,
        )
        admin_ids = [user["id"] for user in self._db.list_users() if role_satisfies(user["role"], "admin")]
        for admin_id in admin_ids:
            self._db.save_system_warning(admin_id, self._project_id, "project_broken", self._error)
        payload = {
            "type": "system_warning", "kind": "project_broken",
            "project_id": self._project_id, "message": self._error,
        }
        for admin_id in admin_ids:
            await self._ws_notifications.push(admin_id, payload)


class ProjectHealthNotifications:
    def __init__(self, db: Db, job_service: JobService, ws_notifications: WsNotifications) -> None:
        self._db = db
        self._job_service = job_service
        self._ws_notifications = ws_notifications

    def register(self) -> None:
        subscribe(ProjectPublishedHealthChanged, self._on_event)

    def _on_event(self, event: ProjectPublishedHealthChanged) -> None:
        try:
            self._job_service.submit(
                ProjectHealthNotificationJob(self._db, self._ws_notifications, event.project_id, event.revision, event.error)
            )
        except Exception:
            logger.exception(
                "Failed to submit the broken-project notification job for '%s' (revision %s).",
                event.project_id, event.revision,
            )
