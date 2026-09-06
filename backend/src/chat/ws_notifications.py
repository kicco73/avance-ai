from __future__ import annotations

import logging

from fastapi import WebSocket, WebSocketDisconnect

from auth.auth_service import SESSION_COOKIE_NAME, AuthService
from session import Session

logger = logging.getLogger(__name__)


class WsNotifications(object):
    def __init__(self, auth_service: AuthService) -> None:
        self._auth_service = auth_service
        # username -> the one open connection shared across every project:
        # the frontend keeps at most one websocket per tab, reused across
        # every project's own chat, so a per-project connection was never needed.
        self._connections: dict[str, WebSocket] = {}

    async def notification_loop(self, websocket: WebSocket) -> None:

        token = websocket.cookies.get(SESSION_COOKIE_NAME)
        identity = self._auth_service.verify_token(token) if token else None
        # role=None means "verified identity, no User row yet" (mid
        # Terms-of-Service flow, see AuthService.verify_token) — same as
        # unauthenticated for chat purposes, just not for every route.
        if identity is None or identity.role is None:
            await websocket.close(code=4401)
            return
        Session().user = identity.email
        Session().role = identity.role

        username = Session().user
        await websocket.accept()
        logger.info(f"accepted websocket for {username}")
        self._connections[username] = websocket
        try:
            while True:
                await websocket.receive()
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