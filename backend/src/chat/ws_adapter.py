from __future__ import annotations

import logging

from fastapi import WebSocket, WebSocketDisconnect

from auth.auth_service import SESSION_COOKIE_NAME, AuthService
from db import Db
from service_error import ServiceError
from session import Session
from .chat_service import ChatService

logger = logging.getLogger(__name__)


class WsAdapter(object):
    def __init__(self, chat_service: ChatService, db: Db, auth_service: AuthService) -> None:
        self._chat_service = chat_service
        self._db = db
        self._auth_service = auth_service
        # username -> the one open connection shared across every project:
        # the frontend keeps at most one websocket per tab, reused across
        # every project's own chat, so a per-project connection was never needed.
        self._connections: dict[str, WebSocket] = {}

    async def chat_loop(self, websocket: WebSocket) -> None:
        """Accepts the /ws/chat connection and dispatches every non-empty
        frame to ChatService.process_turn(), one at a time. The login
        wall's own middleware (see auth/auth_middleware.py) never sees a
        websocket handshake at all, so this checks the same cookie by
        hand — before accept(), so an unauthenticated client gets a
        clean close instead of a connection it can still send frames on."""
        token = websocket.cookies.get(SESSION_COOKIE_NAME)
        identity = self._auth_service.verify_token(token) if token else None
        if identity is None:
            await websocket.close(code=4401)
            return
        Session().user = identity.email

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
        """Sends `payload` to `username`'s single shared connection, if
        one exists. No exception for the no-connection case — a
        dormant/disconnected user just gets False back."""
        websocket = self._connections.get(username)
        if websocket is None:
            return False
        await websocket.send_json(payload)
        return True