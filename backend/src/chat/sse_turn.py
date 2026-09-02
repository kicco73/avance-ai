from __future__ import annotations

import asyncio
import json

from fastapi.responses import StreamingResponse

from logging_factory import LoggerFactory
from service_error import ServiceError
from .chat_service import ChatService

logger = LoggerFactory.get_logger(__name__)


class SseChatTurn(object):
    def __init__(
        self, chat_service: ChatService, session_id: int, text: str | None, extra_prompt: str | None = None
    ) -> None:
        self._chat_service = chat_service
        self._session_id = session_id
        self._text = text
        self._extra_prompt = extra_prompt
        self._events: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()

    async def on_metadata(self, key: str, value) -> None:
        if key == "audio":
            await self._events.put(("audio_text", {"content": value}))
        elif key == "chunk":
            await self._events.put(("chunk", {"content": value}))
        elif key == "text":
            await self._events.put(("text", {"content": value}))

    def response(self) -> StreamingResponse:
        return StreamingResponse(self._stream(), media_type="text/event-stream")

    async def _run(self) -> None:
        try:
            result = await self._chat_service.process_turn(
                self._session_id, self._text, on_metadata=self.on_metadata, extra_prompt=self._extra_prompt
            )
            await self._events.put(("done", result))
        except ServiceError as exc:
            data = {"message": exc.message, "detail": getattr(exc, "detail", str(exc))}
            if exc.code is not None:
                data["code"] = exc.code
            await self._events.put(("error", data))
        except Exception as exc:
            logger.exception(f"Unexpected error while processing a chat turn: {str(exc)}")
            await self._events.put((
                "error",
                {"message": "Unexpected server error.", "detail": str(exc)},
            ))

    async def _stream(self):
        turn = asyncio.ensure_future(self._run())
        try:
            while True:
                event, data = await self._events.get()
                yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
                if event in ("done", "error"):
                    return
        finally:
            await turn
