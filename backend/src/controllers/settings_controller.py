"""The Settings menu's own backend surface — whole-database backup/
restore, Manage projects' own table (runtime status, manual pause/
resume), and a project's own lifecycle as a whole object (list, create,
switch, download/upload, delete) rather than any one field inside it
(see edit_project_controller.py for that half). Split out of what used
to be one single AvanceController class in controller.py — see that
module's own docstring, and BaseController's for the shared registration
mechanism/ordering-constraint notes every *_controller.py shares.
"""
from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request, Response

from chat.chat_service import ChatService
from db import Db
from project.project_service import ProjectService

from .base_controller import BaseController, delete, get, post, put


class SettingsController(BaseController):

    def __init__(self, chat_service: ChatService, project_service: ProjectService, db: Db) -> None:
        self.chat_service = chat_service
        self.project_service = project_service
        self.db = db

    @get("/api/settings/backup")
    async def get_backup(self):
        """Downloads the whole working SQLite database file — every
        project, session, message, and signal — as a restorable backup
        (see POST /api/settings/backup)."""
        async with self.chat_service.lock:
            content = self.db.export_backup()
        filename = Path(self.db.backup_file_path()).stem + ".sqlite"
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @post("/api/settings/backup")
    async def post_backup(self, request: Request):
        """Restores the working SQLite database from an uploaded backup
        file — replaces it in place, at the exact path this server is
        configured to use (see config.database_url). Wipes whatever the
        server currently has (all projects, sessions, messages)."""
        content = await request.body()
        async with self.chat_service.lock:
            try:
                self.db.restore_backup(content)
            except ValueError as exc:
                raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
            self.chat_service.tracking_service.clear_auto_tracking_overrides()
        return {"success": True}

    @get("/api/projects")
    def get_projects(self):
        return self.project_service.list_projects()

    @get("/api/settings/projects/runtime-status")
    def get_all_projects_runtime_status(self):
        """One row per project — name/status/paused_reason/revision/
        published_revision — the Settings > Runtime status view's own
        table."""
        return {"projects": self.project_service.get_runtime_status()}

    @put("/api/projects/{project_name}/pause")
    def put_project_pause(self, project_name: str):
        """An operator's own explicit override — only ever allowed while
        `project_name` is actually running (see ProjectService.
        set_manually_paused, which enforces this itself, same reason the
        Runtime status view's own status button disables itself for
        every other status)."""
        try:
            return self.project_service.set_manually_paused(project_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @put("/api/projects/{project_name}/resume")
    def put_project_resume(self, project_name: str):
        """The other half of pause above — only ever allowed while
        `project_name` is manually paused (see ProjectService.
        set_manually_running)."""
        try:
            return self.project_service.set_manually_running(project_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/projects")
    async def post_new_project(self):
        """"New project" — same effect as PUT /api/projects/{project_name}
        with backend/samples/Hello world.zip as the body, minus picking a
        name first (see ProjectService.create_new_project)."""
        return await self.project_service.create_new_project(self._activate_project)

    @put("/api/projects/{project_name}/activate")
    async def activate_project(self, project_name: str):
        try:
            await self.project_service.activate_project_idempotent(project_name, self._activate_project)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {
            "success": True,
            "project_name": project_name,
        }

    @get("/api/projects/{project_name}")
    def get_project(self, project_name: str):
        """Downloads `project_name` as a zip — the read side of PUT
        /api/projects/{project_name}, built so it round-trips back through PUT
        with no transformation. Not restricted to the active project."""
        try:
            content = self.project_service.export_project_zip(project_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        encoded_project_name = quote(project_name)
        return Response(
            content=content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="project.zip"; filename*=UTF-8\'\'{encoded_project_name}.zip'
            },
        )

    @put("/api/projects/{project_name}")
    async def put_project(self, project_name: str, request: Request):
        """Creates or replaces `project_name` from a raw body (YAML or zip, see
        ProjectService._looks_like_zip). Stage -> validate -> only on success
        commit, swap, and wipe `project_name`'s prior conversation data."""
        content = await request.body()
        content_type = request.headers.get("content-type")

        try:
            result = await self.project_service.put_project(project_name, content, content_type, self._activate_project)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return result

    @delete("/api/projects/{project_name}")
    async def delete_project(self, project_name: str):

        try:
            await self.project_service.delete_project(project_name, self._activate_project)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
        return {"success": True}
