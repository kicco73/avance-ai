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
    def __init__(
        self, db: Db, ws_notifications: WsNotifications, project_id: str, revision: int, error: str | None, *,
        file: str | None = None, line: int | None = None,
    ) -> None:
        super().__init__(key=f"project-health:{project_id}:{revision}:{error is not None}", username="system")
        self._db = db
        self._ws_notifications = ws_notifications
        self._project_id = project_id
        self._revision = revision
        self._error = error
        self._file = file
        self._line = line

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        return 1, ()

    @property
    def is_background(self) -> bool:
        return True

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        admin_ids = [user["id"] for user in self._db.list_users() if role_satisfies(user["role"], "admin")]
        if self._error is None:
            await self._report_recovered(admin_ids)
        else:
            await self._report_broken(admin_ids)

    async def _report_recovered(self, admin_ids: list[str]) -> None:
        logger.info("Project '%s' revision %s builds again.", self._project_id, self._revision)
        self._db.delete_project_system_warnings(self._project_id, "project_broken")
        await self._push_to_admins(
            admin_ids, {"type": "system_warning", "kind": "project_fixed", "project_id": self._project_id},
        )

    async def _report_broken(self, admin_ids: list[str]) -> None:
        logger.error(
            "Project '%s' revision %s no longer builds — %s", self._project_id, self._revision, self._error,
        )
        for admin_id in admin_ids:
            self._db.save_system_warning(
                admin_id, self._project_id, "project_broken", self._error, file=self._file, line=self._line,
            )
        await self._push_to_admins(admin_ids, {
            "type": "system_warning", "kind": "project_broken",
            "project_id": self._project_id, "message": self._error, "file": self._file, "line": self._line,
        })

    async def _push_to_admins(self, admin_ids: list[str], payload: dict) -> None:
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
                ProjectHealthNotificationJob(
                    self._db, self._ws_notifications, event.project_id, event.revision, event.error,
                    file=event.file, line=event.line,
                )
            )
        except Exception:
            logger.exception(
                "Failed to submit the broken-project notification job for '%s' (revision %s).",
                event.project_id, event.revision,
            )
