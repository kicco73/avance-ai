from __future__ import annotations

import asyncio
import json

from fastapi.responses import StreamingResponse

from logging_factory import LoggerFactory
from service_error import ServiceError
from .chat_service import ChatService
from .tool_status_text import tool_status_text

logger = LoggerFactory.get_logger(__name__)


class SseChatTurn(object):
    def __init__(self, chat_service: ChatService, session_id: int, text: str | None) -> None:
        self._chat_service = chat_service
        self._session_id = session_id
        self._text = text
        self._events: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()

    def on_metadata(self, key: str, value) -> None:
        if key == "audio":
            self._events.put_nowait(("audio_text", {"content": value}))
        elif key == "chunk":
            self._events.put_nowait(("chunk", {"content": value}))
        elif key == "text":
            self._events.put_nowait(("text", {"content": value}))
        elif key == "tool":
            # One SSE event, "tool", for both phases — the frontend's own
            # reader (chatClient.js) tells them apart by data.phase.
            # status_text is only ever meaningful on "start" (see
            # tool_status_text's own docstring); "result" carries the
            # payload verbatim.
            payload = {**value, "status_text": tool_status_text(value)} if value["phase"] == "start" else value
            self._events.put_nowait(("tool", payload))

    def response(self) -> StreamingResponse:
        return StreamingResponse(self._stream(), media_type="text/event-stream")

    async def _run(self) -> None:
        try:
            result = await self._chat_service.process_turn(
                self._session_id, self._text, on_metadata=self.on_metadata
            )
            self._events.put_nowait(("done", result))
        except ServiceError as exc:
            data = {"message": exc.message, "detail": getattr(exc, "detail", str(exc))}
            if exc.code is not None:
                data["code"] = exc.code
            self._events.put_nowait(("error", data))
        except Exception as exc:
            logger.exception(f"Unexpected error while processing a chat turn: {str(exc)}")
            self._events.put_nowait((
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
