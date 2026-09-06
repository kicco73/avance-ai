from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from auth.auth_service import SESSION_COOKIE_NAME, AuthService
from session import Session
from .channels import NATIVE_CHAT
from .chat_service import ChatService
from .ws_turn import WsChatTurn

logger = logging.getLogger(__name__)


class WsConnection(object):
    """One open websocket: every outgoing frame — a turn's own chunks,
    a pushed notification, a pong — goes through send(), onto a single
    queue drained by the one writer task, so frames never interleave on
    the wire and a synchronous caller (a turn's on_metadata) never has
    to await. Once the socket is gone, send() discards at DEBUG."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._outgoing: asyncio.Queue[dict | None] = asyncio.Queue()
        self._closed = False

    def send(self, payload: dict) -> None:
        if self._closed:
            logger.debug(f"websocket gone, frame discarded: type={payload.get('type')} turn_id={payload.get('turn_id')}")
            return
        self._outgoing.put_nowait(payload)

    async def write_loop(self) -> None:
        try:
            while True:
                payload = await self._outgoing.get()
                if payload is None:
                    return
                await self._websocket.send_json(payload)
        except Exception as exc:
            logger.debug(f"websocket writer stopped: {exc}")
        finally:
            self._closed = True

    def close(self) -> None:
        self._closed = True
        self._outgoing.put_nowait(None)


class WsNotifications(object):
    """The one websocket per user, both directions: the browser sends
    `turn` frames on it (the only inbound chat frame — actions, session
    bootstrap and everything else stay HTTP) and receives every frame
    the server has for it — a turn's own chunk/tool/done/error, each
    carrying the turn_id of the `turn` frame that produced it, plus the
    push-only notification/test_update/system_warning frames.

    Ordering guarantee: one receive loop per socket reads `turn` frames
    in arrival order, and each user message is persisted right there, in
    reading order, before any processing starts. The turn itself then
    runs as a task; the session lock serializes turns of one session."""

    def __init__(self, auth_service: AuthService, chat_service: ChatService | None = None) -> None:
        self._auth_service = auth_service
        self._chat_service = chat_service
        # username -> the one open connection shared across every project:
        # the frontend keeps at most one websocket per tab, reused across
        # every project's own chat, so a per-project connection was never needed.
        self._connections: dict[str, WsConnection] = {}
        self._turn_tasks: set[asyncio.Task] = set()

    async def channel_loop(self, websocket: WebSocket) -> None:
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
        Session().channel = NATIVE_CHAT

        username = Session().user
        await websocket.accept()
        logger.info(f"accepted websocket for {username}")
        connection = WsConnection(websocket)
        self._connections[username] = connection
        writer = asyncio.create_task(connection.write_loop())
        try:
            while True:
                raw = await websocket.receive_text()
                self._handle_frame(connection, raw)
        except WebSocketDisconnect:
            pass
        finally:
            if self._connections.get(username) is connection:
                del self._connections[username]
            connection.close()
            await writer

    def _handle_frame(self, connection: WsConnection, raw: str) -> None:
        try:
            frame = json.loads(raw)
        except ValueError:
            logger.debug(f"ignoring a non-JSON websocket frame: {raw[:80]!r}")
            return
        if not isinstance(frame, dict):
            return
        frame_type = frame.get("type")
        if frame_type == "ping":
            connection.send({"type": "pong"})
        elif frame_type == "turn":
            self._start_turn(connection, frame)
        else:
            logger.debug(f"ignoring an unknown websocket frame type: {frame_type!r}")

    def _start_turn(self, connection: WsConnection, frame: dict) -> None:
        if self._chat_service is None:
            return
        turn = WsChatTurn(
            self._chat_service, connection, str(frame.get("turn_id", "")), frame.get("session_id"),
            str(frame.get("text", "")),
        )
        if not turn.accept():
            return
        task = asyncio.create_task(turn.run())
        self._turn_tasks.add(task)
        task.add_done_callback(self._turn_tasks.discard)

    async def push(self, username: str, payload: dict) -> bool:
        """Sends `payload` to `username`'s single shared connection, if
        one exists. No exception for the no-connection case — a
        dormant/disconnected user just gets False back."""
        connection = self._connections.get(username)
        if connection is None:
            return False
        connection.send(payload)
        return True
