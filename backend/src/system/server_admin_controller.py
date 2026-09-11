"""Running the server, as opposed to authoring what it runs.

Core, not platform. Backing the database up, restoring it, wiping live
sessions, clearing revisions nothing points at any more, reading what the
scheduler has queued, reading which services this deployment has
configured: an operator needs every one of these whether or not an editor
was ever installed, and none of them touches a project's contents.

`settings/services` in particular is the snapshot each installed skill
describes itself into (bus.POINT_CONFIG_SERVICES), the way
api_state_controller.py assembles POINT_API_STATE — an assembly point
belongs to whoever assembles, and that is the core.

What stays with the authoring surface is what manages *projects*: the
list, the pause switch, the upload, the broken-project warnings. Those
are in the Settings controller that surface registers for itself, which
is where the routes below came from.
"""
from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response

from controllers.base_controller import BaseController, get, post
from db import Db
from project.project_service import ProjectService
from scheduler import SchedulerService
from turn.turn_service import TurnService

APP_NAME = "Avance"


class ServerAdminController(BaseController):

    def __init__(
        self, turn_service: TurnService, project_service: ProjectService, db: Db,
        version: str, scheduler_service: SchedulerService, services_config: dict,
    ) -> None:
        self.turn_service = turn_service
        self.project_service = project_service
        self.db = db
        self.version = version
        self.scheduler_service = scheduler_service
        self.services_config = services_config

    def register_routes(self, router: APIRouter) -> None:
        for method, path, kwargs, member in self._declared_routes():
            router.add_api_route(path, member, methods=[method], **kwargs)

    @get("/api/core/settings/about", role="supervisor")
    def get_about(self):
        return {"name": APP_NAME, "version": self.version}

    @get("/api/core/settings/services", role="admin")
    def get_services(self):
        """Read-only snapshot of .config.yml's own service sections (see
        AppConfig.public_services_snapshot), one tab per section on the
        frontend."""
        return self.services_config

    @get("/api/core/settings/services/ai-usage", role="admin")
    def get_ai_usage(self):
        """Each ai-service provider's own token spend, one point per
        minute over the trailing 24h (see db/ai_usage.py)."""
        labels = [f"{p['driver']}/{p['model']}" for p in self.services_config["ai"]["providers"]]
        return self.db.get_ai_token_usage_snapshot(labels)

    @get("/api/core/settings/tasks", role="admin")
    def get_scheduled_tasks(self, status: str | None = None, order: str = "asc"):
        """Task rows for one status at a time, by run_at per `order` (see
        scheduler.SchedulerService.list_tasks). `payload` is omitted: it
        is the task type's own hydration data, not meant for display."""
        try:
            tasks = self.scheduler_service.list_tasks(status=status, order=order)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {
            "tasks": [
                {key: value for key, value in task.items() if key != "payload"}
                for task in tasks
            ]
        }

    @get("/api/core/settings/backup", role="admin")
    async def get_backup(self):
        """Downloads the whole working SQLite database file — every
        project, session, message, and signal — as a restorable backup
        (see POST /api/core/settings/backup)."""
        async with self.turn_service.global_exclusive_access():
            content = self.db.export_backup()
        filename = Path(self.db.backup_file_path()).stem + ".sqlite"
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @post("/api/core/settings/backup", role="admin")
    async def post_backup(self, request: Request):
        """Restores the working SQLite database from an uploaded backup
        file, replacing it in place. Wipes whatever the server currently
        has (all projects, sessions, messages)."""
        content = await request.body()
        async with self.turn_service.global_exclusive_access():
            try:
                self.db.restore_backup(content)
            except ValueError as exc:
                raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
            self.turn_service.clear_auto_tracking_overrides()
        return {"success": True}

    @post("/api/core/settings/database/wipe-live-sessions", role="admin")
    async def post_wipe_all_live_sessions(self):
        """Deletes every live conversation across every project (not just
        the active one), same global scope as the backup endpoints."""
        async with self.turn_service.global_exclusive_access():
            self.project_service.manager.wipe_all_live_sessions()
        return {"success": True}

    @post("/api/core/settings/database/clean-unused-revisions", role="admin")
    async def post_clean_unused_revisions(self):
        """Deletes every archive revision, across every project, that is
        neither published, the current draft, nor pinned by any session
        (see ProjectManager.clean_unused_revisions)."""
        async with self.turn_service.global_exclusive_access():
            deleted = self.project_service.manager.clean_unused_revisions()
        return {"success": True, "deleted": deleted}
