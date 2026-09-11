"""Talk's own HTTP surface: one route, and it exists only when this
package is in the build.

It used to live in controllers/chat_controller.py, which meant a build
without `src/talk/` still answered GET /api/skills/talk/messages/{id}/audio —
with a 503, but it answered, and a route that answers is a route that
says the feature exists. Here the route is registered by talk/skill.py
(bus.POINT_HTTP_CONTROLLERS) and leaving the package out leaves nothing
behind to answer.
"""
from __future__ import annotations

from http import HTTPStatus

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from controllers.base_controller import BaseController, get
from talk.talk_service import TalkService
from turn.turn_service import TurnService


class TalkController(BaseController):
    """Holds the TalkService directly rather than going through the Bus:
    this route only exists when the package that answers it is here, and
    it streams the audio out chunk by chunk as it is generated — which a
    single message carrying one finished body cannot do."""

    def __init__(self, turn_service: TurnService, talk_service: TalkService) -> None:
        self.turn_service = turn_service
        self.talk_service = talk_service

    @get("/api/skills/talk/messages/{message_id}/audio")
    def get_message_audio(self, message_id: int, request: Request):
        """Generates (or replays a cached/in-flight) audio for message_id,
        streaming-compatible. 404 if the message had no [audio] tag — the
        frontend treats that as "no audio available", not a failure."""
        audio_text = self.turn_service.get_message_audio_text(message_id)
        if not audio_text:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="No audio available for this message.")
        return StreamingResponse(
            self._stream_audio_until_disconnected(request, self.talk_service.generate(audio_text)),
            media_type="audio/wav",
        )

    async def _stream_audio_until_disconnected(self, request: Request, generation):
        # A dropped/aborted fetch doesn't reliably surface as a send()
        # failure — polling is_disconnected() stops the provider's work
        # immediately instead of wasting a full synthesis.
        try:
            async for chunk in generation:
                if await request.is_disconnected():
                    break
                yield chunk
        finally:
            aclose = getattr(generation, "aclose", None)
            if aclose and callable(aclose):
                aclose()
