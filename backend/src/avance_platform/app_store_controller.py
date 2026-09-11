from __future__ import annotations

import hashlib
from http import HTTPStatus

from fastapi import HTTPException, Request, Response

from turn.turn_service import TurnService
from avance_platform.platform_service import PlatformService
from system.web_session import WebSession

from controllers.base_controller import BaseController, delete, get, post


class AppStoreController(BaseController):

    def __init__(self, turn_service: TurnService, platform_service: "PlatformService") -> None:
        self.turn_service = turn_service
        self.platform_service = platform_service

    @get("/api/skills/platform/app-store/apps")
    def get_apps(self, q: str | None = None):
        return {"apps": self.platform_service.list_app_store_apps(WebSession().user, q)}

    @post("/api/skills/platform/app-store/apps/{app_id}/install")
    def post_install_app(self, app_id: str):
        try:
            self.platform_service.install_app(WebSession().user, app_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        return {"success": True}

    @delete("/api/skills/platform/app-store/apps/{app_id}/install")
    def delete_install_app(self, app_id: str):
        self.platform_service.uninstall_app(WebSession().user, app_id)
        return {"success": True}

    @get("/api/skills/platform/app-store/apps/{app_id}/preview-transcript")
    def get_app_preview_transcript(self, app_id: str):
        return {"messages": self.platform_service.get_app_store_preview_messages(app_id)}

    @get("/api/skills/platform/app-store/apps/{app_id}/session-summaries")
    def get_app_session_summaries(self, app_id: str):
        return {"sessions": self.platform_service.get_app_session_summaries(WebSession().user, app_id)}

    @get("/api/skills/platform/app-store/apps/{app_id}/files/{file_name:path}/content")
    def get_app_file_content(self, app_id: str, file_name: str, request: Request):
        try:
            content, content_type = self.platform_service.get_app_store_file_content(app_id, file_name)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        etag = f'"{hashlib.sha256(content).hexdigest()}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=HTTPStatus.NOT_MODIFIED, headers={"ETag": etag, "Cache-Control": "no-cache"})
        return Response(content=content, media_type=content_type, headers={"ETag": etag, "Cache-Control": "no-cache"})

    @post("/api/skills/platform/app-store/apps/{app_id}/preview-sessions")
    async def post_create_preview_session(self, app_id: str):
        return await self.turn_service.create_preview_session(app_id)

    @get("/api/skills/platform/app-store/apps/{app_id}/preview-sessions/current")
    async def get_current_preview_session(self, app_id: str, session_id: int | None = None):
        return await self.turn_service.get_current_preview_session_if_any_or_create_new(session_id, app_id)

    @delete("/api/skills/platform/app-store/preview-sessions/{session_id}/env")
    async def delete_preview_session_env(self, session_id: int):
        self.turn_service.clear_session_env(session_id)
        return {"success": True}
