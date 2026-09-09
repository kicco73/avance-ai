"""Listen's own route, in Listen's own package.

It used to live in StudioController, which is why removing the directory
would have broken the core: a route written inside a shared file cannot
be excluded by not composing an object. Here it can — nothing registers
it unless this package is present.
"""
from __future__ import annotations

from http import HTTPStatus

from fastapi import HTTPException, UploadFile

from controllers.base_controller import BaseController, post
from listen.listen_service import ListenService, ListenServiceError


class ListenController(BaseController):

    def __init__(self, listen_service: ListenService) -> None:
        self.listen_service = listen_service

    @post("/api/listen/transcribe")
    async def post_listen_transcribe(self, file: UploadFile):
        """Isolated verification endpoint: not wired into process_turn or
        the chat frontend — a voice note reaches a turn through the Bus
        (see listen.decoder). This confirms the service end-to-end."""
        audio_bytes = await file.read()
        try:
            text = await self.listen_service.transcribe(audio_bytes)
        except ListenServiceError as exc:
            raise HTTPException(status_code=HTTPStatus.SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        return {"text": text}
