from __future__ import annotations

import logging

from fastapi import WebSocket, WebSocketDisconnect

from db import Db
from service_error import ServiceError
from .chat_service import ChatService

logger = logging.getLogger(__name__)


class WsAdapter(object):
    def __init__(self, chat_service: ChatService, db: Db) -> None:
        self._chat_service = chat_service
        self._db = db
        # (username, project_name) -> the one open connection for that
        # pair — no longer "at most one connection matters" (Prompt 12):
        # WakeupService's own cross-project wake-up needs a way to reach
        # a connected client whose *active* project isn't the one that
        # just changed (see push below), which a single global socket
        # could never distinguish. Keyed by (username, project_name)
        # rather than just username since a wake-up push is always about
        # one specific *other* project's own dormant session, never
        # "whichever project this user happens to have open right now".
        self._connections: dict[tuple[str, str], WebSocket] = {}

    async def chat_loop(self, websocket: WebSocket) -> None:
        """Accepts the /ws/chat connection and dispatches every non-empty
        frame to ChatService.process_turn(), one at a time (the loop only
        calls receive_json() again once the previous turn is fully done).
        Registers this socket under (username, project_name) — resolved
        fresh off each frame's own session_id (db.get_chat_session), not
        just once at accept() — a project_name isn't knowable at accept()
        time at all, before the first frame's session_id has even
        arrived."""
        await websocket.accept()
        try:
            while True:
                data = await websocket.receive_json()
                text = (data or {}).get("message", "").strip()
                session_id = (data or {})["session_id"]
                session = self._db.get_chat_session(session_id)
                if session is not None:
                    self._connections[(session["username"], session["project_name"])] = websocket
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
            for key in [k for k, v in self._connections.items() if v is websocket]:
                del self._connections[key]

    async def push(self, username: str, project_name: str, payload: dict) -> bool:
        """Sends `payload` straight to (username, project_name)'s own
        open connection, if one exists — WakeupService's own way to
        deliver a cross-project self-loop transition to a client that
        isn't actively mid-turn on it (see _reevaluate_and_apply). No
        exception, and no special-casing, for the common case of no
        connection at all: a dormant/disconnected session is a perfectly
        normal outcome here, not an error — the caller just gets False
        back and moves on, same as it always would have before there was
        anyone to push to."""
        websocket = self._connections.get((username, project_name))
        if websocket is None:
            return False
        await websocket.send_json(payload)
        return True