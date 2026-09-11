"""Operating the deployment: the whole database in and out, wiping live
conversations, clearing revisions nothing points at.

Only the operations. Reading what this deployment has configured, what
the scheduler has queued, what it is spending and what version it runs is
*describing*, stays true with no panel in the build, and is answered by
the core (system/api_state_controller.py). The line is describe/decide,
and a backup is squarely on the far side of it: nothing about the domain
changes because nobody asked for one.

None of it is domain. A session, a project, an automaton, a user: those
exist because the system does what it does, and they are the core's. A
*backup* exists because somebody administers the server — it is an
operation on the deployment, offered by a panel, and the panel is this
package. A product delivered without that panel is not administered by
itself; it is administered by whoever has one.

These routes were briefly core, on the reasoning that an operator needs
them whether or not an editor was installed. That reasoning measured the
services the handler calls (db, turn_service) instead of what the caller
is doing, and by it everything is core, since everything ends up at the
db eventually.
"""
from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response

from controllers.base_controller import BaseController, get, post
from db import Db
from project.project_service import ProjectService
from turn.turn_service import TurnService

class ServerAdminController(BaseController):

    def __init__(self, turn_service: TurnService, project_service: ProjectService, db: Db) -> None:
        self.turn_service = turn_service
        self.project_service = project_service
        self.db = db

    def register_routes(self, router: APIRouter) -> None:
        for method, path, kwargs, member in self._declared_routes():
            router.add_api_route(path, member, methods=[method], **kwargs)

    @get("/api/skills/platform/settings/backup", role="admin")
    async def get_backup(self):
        """Downloads the whole working SQLite database file — every
        project, session, message, and signal — as a restorable backup
        (see POST /api/skills/platform/settings/backup)."""
        async with self.turn_service.global_exclusive_access():
            content = self.db.export_backup()
        filename = Path(self.db.backup_file_path()).stem + ".sqlite"
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @post("/api/skills/platform/settings/backup", role="admin")
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

    @post("/api/skills/platform/settings/database/wipe-live-sessions", role="admin")
    async def post_wipe_all_live_sessions(self):
        """Deletes every live conversation across every project (not just
        the active one), same global scope as the backup endpoints."""
        async with self.turn_service.global_exclusive_access():
            self.project_service.manager.wipe_all_live_sessions()
        return {"success": True}

    @post("/api/skills/platform/settings/database/clean-unused-revisions", role="admin")
    async def post_clean_unused_revisions(self):
        """Deletes every archive revision, across every project, that is
        neither published, the current draft, nor pinned by any session
        (see ProjectManager.clean_unused_revisions)."""
        async with self.turn_service.global_exclusive_access():
            deleted = self.project_service.manager.clean_unused_revisions()
        return {"success": True, "deleted": deleted}
