from __future__ import annotations

import hashlib
from http import HTTPStatus

from fastapi import HTTPException, Request, Response

from chat.chat_service import ChatService
from project.project_service import ProjectService
from session import Session

from .base_controller import BaseController, delete, get, post


class AppStoreController(BaseController):

    def __init__(self, chat_service: ChatService, project_service: ProjectService) -> None:
        self.chat_service = chat_service
        self.project_service = project_service

    @get("/api/app-store/apps")
    def get_apps(self, q: str | None = None):
        return {"apps": self.project_service.list_app_store_apps(Session().user, q)}

    @post("/api/app-store/apps/{app_id}/install")
    def post_install_app(self, app_id: str):
        try:
            self.project_service.install_app(Session().user, app_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        return {"success": True}

    @delete("/api/app-store/apps/{app_id}/install")
    def delete_install_app(self, app_id: str):
        self.project_service.uninstall_app(Session().user, app_id)
        return {"success": True}

    @get("/api/app-store/apps/{app_id}/preview-transcript")
    def get_app_preview_transcript(self, app_id: str):
        return {"messages": self.project_service.get_app_store_preview_messages(app_id)}

    @get("/api/app-store/apps/{app_id}/session-summaries")
    def get_app_session_summaries(self, app_id: str):
        return {"sessions": self.project_service.get_app_session_summaries(Session().user, app_id)}

    @get("/api/app-store/apps/{app_id}/files/{file_name:path}/content")
    def get_app_file_content(self, app_id: str, file_name: str, request: Request):
        try:
            content, content_type = self.project_service.get_app_store_file_content(app_id, file_name)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        etag = f'"{hashlib.sha256(content).hexdigest()}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=HTTPStatus.NOT_MODIFIED, headers={"ETag": etag, "Cache-Control": "no-cache"})
        return Response(content=content, media_type=content_type, headers={"ETag": etag, "Cache-Control": "no-cache"})

    @post("/api/app-store/apps/{app_id}/preview-sessions")
    async def post_create_preview_session(self, app_id: str):
        return await self.chat_service.create_preview_session(app_id)

    @get("/api/app-store/apps/{app_id}/preview-sessions/current")
    async def get_current_preview_session(self, app_id: str, session_id: int | None = None):
        return await self.chat_service.get_current_preview_session_if_any_or_create_new(session_id, app_id)

    @delete("/api/app-store/preview-sessions/{session_id}/env")
    async def delete_preview_session_env(self, session_id: int):
        self.chat_service.clear_session_env(session_id)
        return {"success": True}
