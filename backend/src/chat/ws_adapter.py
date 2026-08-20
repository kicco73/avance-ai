from __future__ import annotations

import logging

from fastapi import WebSocket, WebSocketDisconnect

from db import Db
from service_error import ServiceError
from session import Session
from .chat_service import ChatService

logger = logging.getLogger(__name__)


class WsAdapter(object):
    def __init__(self, chat_service: ChatService, db: Db) -> None:
        self._chat_service = chat_service
        self._db = db
        # username -> the one open connection shared across every project
        # (Prompt 13 — correction to Prompt 12's own (username,
        # project_name) keying): the frontend already keeps at most one
        # websocket per tab, reused across every project's own chat, so a
        # separate connection per project was never needed — and keying
        # on it meant a project that was only ever opened, never written
        # to (e.g. an initial greeting served over REST), stayed
        # unregistered and unreachable by push for its entire lifetime.
        self._connections: dict[str, WebSocket] = {}

    async def chat_loop(self, websocket: WebSocket) -> None:
        """Accepts the /ws/chat connection and dispatches every non-empty
        frame to ChatService.process_turn(), one at a time (the loop only
        calls receive_json() again once the previous turn is fully done).
        Registers this socket under Session().user immediately after
        accept() — Session is a process-wide singleton whose user is
        always already known, so unlike the old per-project keying there's
        no need to wait for a frame's own session_id before registering."""
        username = Session().user
        await websocket.accept()
        self._connections[username] = websocket
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
            if self._connections.get(username) is websocket:
                del self._connections[username]

    async def push(self, username: str, payload: dict) -> bool:
        """Sends `payload` straight to `username`'s own single shared
        connection, if one exists — WakeupService's own way to deliver a
        cross-project self-loop transition to a client that isn't
        actively mid-turn on the project that changed (see
        _reevaluate_and_apply). No exception, and no special-casing, for
        the common case of no connection at all: a dormant/disconnected
        session is a perfectly normal outcome here, not an error — the
        caller just gets False back and moves on, same as it always would
        have before there was anyone to push to."""
        websocket = self._connections.get(username)
        if websocket is None:
            return False
        await websocket.send_json(payload)
        return True