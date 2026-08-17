from __future__ import annotations

import logging

from fastapi import WebSocket, WebSocketDisconnect

from service_error import ServiceError
from .chat_service import ChatService

logger = logging.getLogger(__name__)


class WsAdapter(object):
    def __init__(self, chat_service: ChatService) -> None:
        self._chat_service = chat_service
        # Single-user prototype: at most one connection matters.
        self._active_socket: WebSocket | None = None

    async def chat_loop(self, websocket: WebSocket) -> None:
        """Accepts the /ws/chat connection and dispatches every non-empty
        frame to ChatService.process_turn(), one at a time (the loop only
        calls receive_json() again once the previous turn is fully done)."""
        await websocket.accept()
        self._active_socket = websocket
        try:
            while True:
                data = await websocket.receive_json()
                text = (data or {}).get("message", "").strip()
                session_id = (data or {})["session_id"]
                if not text:
                    continue

                async def _push_retrying(attempt: int, max_attempts: int, retry_in: float) -> None:
                    await websocket.send_json({
                        "type": "retrying",
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "retry_in": retry_in,
                    })

        
                async def _push_metadata(key: str, value) -> None:
                    if key == "audio":
                        await websocket.send_json({
                            "type": "audio_text",
                            "content": value,
                        })
                    elif key == "chunk":
                        await websocket.send_json({
                            "type": "chunk",
                            "content": value,
                        })
                    elif key == "text":
                        await websocket.send_json({
                            "type": "text",
                            "content": value,
                        })

                try:
                    
                    result = await self._chat_service.process_turn(
                        session_id,
                        text,
                        on_metadata=_push_metadata,
                    )

                except ServiceError as exc:
                    await websocket.send_json({
                        "type": "error",
                        "error": {"message": exc.message, "detail": getattr(exc, "detail", str(exc))},
                    })
                    continue
                except Exception as exc:
                    logger.exception(f"Unexpected error while processing a chat turn: {str(exc)}")
                    await websocket.send_json({
                        "type": "error",
                        "error": {"message": "Unexpected server error.", "detail": str(exc)},
                    })
                    continue

                await websocket.send_json({"type": "done", **result})
        except WebSocketDisconnect:
            pass
        finally:
            if self._active_socket is websocket:
                self._active_socket = None