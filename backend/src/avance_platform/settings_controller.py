"""Managing *projects* from the Settings menu: Manage projects' own table
(runtime status, manual pause/resume), the broken-project warnings it
counts, and a project's lifecycle as a whole object (list, create, switch,
download/upload, delete) rather than any one field inside it (see
edit_project_controller.py for that half).

Running the *server* left for ServerAdminController: backup and
restore, wiping live sessions, clearing unused revisions, the scheduler's
queue, the services snapshot. An operator needs those whether or not an
editor was ever installed, and none of them touches a project's contents.
"""
from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request, Response

from auth.roles import role_satisfies
from turn.turn_service import TurnService
from db import Db
from avance_platform.platform_service import PlatformService
from project.project_service import ProjectService
from scheduler import SchedulerService
from system.web_session import WebSession

from controllers.base_controller import BaseController, delete, get, post, put
from .project_commit_mixin import ProjectCommitMixin


APP_NAME = "Avance"


class SettingsController(BaseController, ProjectCommitMixin):

    def __init__(
        self, turn_service: TurnService, project_service: ProjectService,
        platform_service: PlatformService, db: Db, scheduler_service: SchedulerService,
    ) -> None:
        # Read by ProjectCommitMixin rather than by anything below.
        self.turn_service = turn_service
        self.project_service = project_service
        self.platform_service = platform_service
        self.db = db
        self.scheduler_service = scheduler_service

    @get("/api/skills/platform/settings/projects/runtime-status", role="admin")
    def get_all_projects_runtime_status(self):
        """One row per project — id/status/paused_reason/revision/
        published_revision — the Settings > Runtime status view's own
        table."""
        return {"projects": self.platform_service.get_runtime_status()}

    @get("/api/skills/platform/settings/warnings", role="admin")
    def get_warnings(self, kind: str | None = None):
        """Manage projects' own "broken project" warnings counter/list —
        a durable audit trail of every SystemWarning this admin has
        received (see project/health_notifications.py for kind=
        "project_broken"), even past the project actually being fixed."""
        return {"warnings": self.db.list_system_warnings_for_user(WebSession().user, kind=kind)}

    @delete("/api/skills/platform/settings/warnings/{warning_id}", role="admin")
    def delete_warning(self, warning_id: int):
        if not self.db.delete_system_warning(WebSession().user, warning_id):
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=f"Warning {warning_id} not found.")
        return {"status": "ok"}

    @post("/api/skills/platform/projects/{project_id}/pause", role="admin")
    def put_project_pause(self, project_id: str):
        """An operator's own explicit override — only ever allowed while
        `project_id` is actually running."""
        try:
            return self.platform_service.set_manually_paused(project_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/skills/platform/projects/{project_id}/resume", role="admin")
    def put_project_resume(self, project_id: str):
        """The other half of pause above — only ever allowed while
        `project_id` is manually paused (see ProjectService.
        set_manually_running)."""
        try:
            return self.platform_service.set_manually_running(project_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/skills/platform/projects", role="admin")
    async def post_new_project(self):
        """"New project" — same effect as POST /api/skills/platform/projects/upload with
        backend/samples/Hello world.zip as the body, minus a real upload
        (see ProjectService.create_new_project — its own project.id is
        always freshly minted, since project.id must be globally unique).
        The built-in template bundles no sessions/test results, so
        there's nothing for the returned job to do — no progress worth
        reporting, plain JSON response, unlike a real upload."""
        result, _job = await self.project_service.create_new_project(self._activate_project)
        return result

    @get("/api/skills/platform/projects/{project_id}", role="admin")
    def get_project(self, project_id: str):
        """Downloads `project_id` as a zip — the read side of POST
        /api/skills/platform/projects/upload, built so it round-trips back through that
        endpoint with no transformation. Not restricted to the active project."""
        try:
            content = self.platform_service.export_project_zip(project_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        encoded_project_id = quote(project_id)
        return Response(
            content=content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="project.zip"; filename*=UTF-8\'\'{encoded_project_id}.zip'
            },
        )

    @post("/api/skills/platform/projects/upload", role="admin")
    async def post_upload_project(self, request: Request):
        """Creates a project from a raw body (YAML or zip), or — when its
        own project.id already names an existing project — adds a new
        revision on top of it instead (see ProjectService.put_project for
        the full accept/reject/auto-publish rules). There is no project id
        in this URL: the uploaded content's own project.id is always what's
        used, never a name requested ahead of time — the server decides,
        and returns it. Stage -> validate -> only on success commit, swap,
        and publish. The project definition itself is staged and committed
        synchronously (fast, and needs the main event loop's chat lock);
        this same response then streams SSE progress for a background Job
        importing whatever sessions.json/tests.json the upload bundled,
        ending with a chunk carrying the final {success, project_id}."""
        content = await request.body()
        content_type = request.headers.get("content-type")

        try:
            _, job = await self.project_service.put_project(content, content_type, self._activate_project)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.scheduler_service.stream_progress(job)

    @delete("/api/skills/platform/projects/{project_id}", role="admin")
    async def delete_project(self, project_id: str):

        try:
            await self.project_service.delete_project(project_id, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
        return {"success": True}
