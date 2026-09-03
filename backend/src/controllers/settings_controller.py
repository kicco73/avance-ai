"""The Settings menu's own backend surface — whole-database backup/
restore, Manage projects' own table (runtime status, manual pause/
resume), and a project's lifecycle as a whole object (list, create,
switch, download/upload, delete) rather than any one field inside it
(see edit_project_controller.py for that half).
"""
from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request, Response

from chat.chat_service import ChatService
from db import Db
from job import JobService
from testing.last_status_broadcaster import LastStatusBroadcaster
from testing.queue_progress_broadcaster import QueueProgressBroadcaster
from project.project_service import ProjectService
from session import Session

from .base_controller import BaseController, delete, get, post, put
from .project_commit_mixin import ProjectCommitMixin


APP_NAME = "Avance"


class SettingsController(BaseController, ProjectCommitMixin):

    def __init__(
        self, chat_service: ChatService, project_service: ProjectService, db: Db, version: str,
        test_event_broadcaster: QueueProgressBroadcaster | LastStatusBroadcaster, job_service: JobService,
        services_config: dict,
    ) -> None:
        self.chat_service = chat_service
        self.project_service = project_service
        self.db = db
        self.version = version
        self.test_event_broadcaster = test_event_broadcaster
        self.job_service = job_service
        self.services_config = services_config

    @get("/api/settings/about", role="supervisor")
    def get_about(self):
        """The Settings menu's own "About Avance..." dialog — just the
        display name and running backend version, __version__ in main.py."""
        return {"name": APP_NAME, "version": self.version}

    @get("/api/settings/services", role="admin")
    def get_services(self):
        """Settings > Manage services — read-only snapshot of
        .config.yml's own service sections (see AppConfig.
        public_services_snapshot), one tab per section on the frontend."""
        return self.services_config

    @get("/api/settings/services/ai-usage", role="admin")
    def get_ai_usage(self):
        """Settings > Manage services > AI — each ai-service provider's
        own daily token spend (see db/ai_usage.py), fetched once when the
        panel opens, same as get_services above: {today, history}."""
        labels = [f"{p['driver']}/{p['model']}" for p in self.services_config["ai"]["providers"]]
        return self.db.get_ai_token_usage_snapshot(labels)

    @get("/api/settings/backup", role="admin")
    async def get_backup(self):
        """Downloads the whole working SQLite database file — every
        project, session, message, and signal — as a restorable backup
        (see POST /api/settings/backup)."""
        async with self.chat_service.global_exclusive_access():
            content = self.db.export_backup()
        filename = Path(self.db.backup_file_path()).stem + ".sqlite"
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @post("/api/settings/backup", role="admin")
    async def post_backup(self, request: Request):
        """Restores the working SQLite database from an uploaded backup
        file, replacing it in place. Wipes whatever the server currently
        has (all projects, sessions, messages)."""
        content = await request.body()
        async with self.chat_service.global_exclusive_access():
            try:
                self.db.restore_backup(content)
            except ValueError as exc:
                raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
            self.chat_service.clear_auto_tracking_overrides()
        return {"success": True}

    @post("/api/settings/database/wipe-live-sessions", role="admin")
    async def post_wipe_all_live_sessions(self):
        """Settings > Manage services > Database — deletes every live
        conversation across every project (not just the active one), same
        global scope as the backup endpoints above."""
        async with self.chat_service.global_exclusive_access():
            self.project_service.wipe_all_live_sessions()
        return {"success": True}

    @post("/api/settings/database/clean-unused-revisions", role="admin")
    async def post_clean_unused_revisions(self):
        """Settings > Manage services > Database — deletes every archive
        revision, across every project, that's neither published, the
        current draft, nor pinned by any session (see
        ProjectService.clean_unused_revisions)."""
        async with self.chat_service.global_exclusive_access():
            deleted = self.project_service.clean_unused_revisions()
        return {"success": True, "deleted": deleted}

    @get("/api/projects")
    def get_projects(self):
        username = Session().user if Session().role == 'user' else None
        return self.project_service.list_projects(username)

    @get("/api/settings/projects/runtime-status", role="admin")
    def get_all_projects_runtime_status(self):
        """One row per project — id/status/paused_reason/revision/
        published_revision — the Settings > Runtime status view's own
        table."""
        return {"projects": self.project_service.get_runtime_status()}

    @get("/api/settings/tasks", role="admin")
    def get_scheduled_tasks(self):
        """Settings > Manage services > Scheduler — every row of the Task
        table (see db/tasks.py's list_tasks), soonest run_at first.
        `payload` is omitted: it's the task type's own internal
        hydration data, not meant for display."""
        return {
            "tasks": [
                {key: value for key, value in task.items() if key != "payload"}
                for task in self.db.list_tasks()
            ]
        }

    @put("/api/projects/{project_id}/pause", role="admin")
    def put_project_pause(self, project_id: str):
        """An operator's own explicit override — only ever allowed while
        `project_id` is actually running."""
        try:
            return self.project_service.set_manually_paused(project_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @put("/api/projects/{project_id}/resume", role="admin")
    def put_project_resume(self, project_id: str):
        """The other half of pause above — only ever allowed while
        `project_id` is manually paused (see ProjectService.
        set_manually_running)."""
        try:
            return self.project_service.set_manually_running(project_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/projects", role="admin")
    async def post_new_project(self):
        """"New project" — same effect as POST /api/projects/upload with
        backend/samples/Hello world.zip as the body, minus a real upload
        (see ProjectService.create_new_project — its own project.id is
        always freshly minted, since project.id must be globally unique).
        The built-in template bundles no sessions/test results, so
        there's nothing for the returned job to do — no progress worth
        reporting, plain JSON response, unlike a real upload."""
        result, _job = await self.project_service.create_new_project(self._activate_project)
        return result

    @put("/api/projects/{project_id}/activate")
    async def activate_project(self, project_id: str):
        try:
            await self.project_service.activate_project_idempotent(project_id, self._activate_project)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {
            "success": True,
            "project_id": project_id,
        }

    @get("/api/projects/{project_id}", role="admin")
    def get_project(self, project_id: str):
        """Downloads `project_id` as a zip — the read side of POST
        /api/projects/upload, built so it round-trips back through that
        endpoint with no transformation. Not restricted to the active project."""
        try:
            content = self.project_service.export_project_zip(project_id)
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

    @post("/api/projects/upload", role="admin")
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
        return self.job_service.stream_progress(job)

    @delete("/api/projects/{project_id}", role="admin")
    async def delete_project(self, project_id: str):

        try:
            await self.project_service.delete_project(project_id, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
        return {"success": True}
