from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import WebSocket, WebSocketDisconnect

from auth.auth_service import SESSION_COOKIE_NAME, AuthService
from auth.roles import role_satisfies
from session import Session
from .channels import NATIVE_CHAT
from .chat_service import ChatService
from .ws_turn import WsChatTurn

logger = logging.getLogger(__name__)

# Close code the frontend must treat specially: don't reconnect, show
# "already open elsewhere" instead (see chatClient.js). 44xx is our own
# application range (4401 is the existing auth-failure code).
ALREADY_CONNECTED_CLOSE_CODE = 4409

# How many concurrent sockets one identity may hold: enough for one tab,
# or for an admin testing HumanTalker to answer their own session from a
# second tab (see talker.human_talker) — not "multi-device support", so
# deliberately small. A connection past the cap is refused outright
# (ALREADY_CONNECTED_CLOSE_CODE), never silently swapped for an older one:
# the user whose tab goes quiet with no explanation is exactly the bug
# this replaces.
MAX_CONNECTIONS_PER_USER = 1
MAX_CONNECTIONS_PER_ADMIN = 2

# How long request_human_reply() waits for any of the user's connections
# to answer before giving up — this is a manual-testing seam (see
# talker.human_talker.HumanTalker), not a production SLA.
HUMAN_REPLY_TIMEOUT_SECONDS = 300.0


class HumanReplyTimeoutError(Exception):
    """No connection of the target user answered a human_prompt within
    HUMAN_REPLY_TIMEOUT_SECONDS."""


class HumanNotConnectedError(Exception):
    """The target user has no open websocket at all — nothing to
    broadcast a human_prompt to."""

    def __init__(self, username: str) -> None:
        super().__init__(f"{username} has no open connection to answer as a human.")


class WsConnection(object):
    """One open websocket: every outgoing frame — a turn's own chunks,
    a pushed notification, a pong — goes through send(), onto a single
    queue drained by the one writer task, so frames never interleave on
    the wire and a synchronous caller (a turn's on_metadata) never has
    to await. Once the socket is gone, send() discards at DEBUG."""

    def __init__(self, websocket: WebSocket) -> None:
        self.id = str(uuid.uuid4())
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
    """Every websocket one identity holds open, both directions: the
    browser sends `turn` frames on it (the only inbound chat frame —
    actions, session bootstrap and everything else stay HTTP) and
    receives every frame the server has for it — a turn's own chunk/
    tool/done/error, each carrying the turn_id of the `turn` frame that
    produced it, plus the push-only notification/test_update/
    system_warning/human_prompt frames, sent to every one of that
    identity's own connections at once (see push()).

    Ordering guarantee: one receive loop per socket reads `turn` frames
    in arrival order, and each user message is persisted right there, in
    reading order, before any processing starts. The turn itself then
    runs as a task; the session lock serializes turns of one session.

    At most MAX_CONNECTIONS_PER_USER connections per identity
    (MAX_CONNECTIONS_PER_ADMIN for an admin) — a connection past the cap
    is refused with ALREADY_CONNECTED_CLOSE_CODE, never silently
    replacing an older one: the tab that goes quiet with no explanation
    is exactly the failure mode this is meant to avoid."""

    def __init__(self, auth_service: AuthService, chat_service: ChatService | None = None) -> None:
        self._auth_service = auth_service
        self._chat_service = chat_service
        # username -> every open connection of that identity, oldest
        # first — see the class docstring for the cap.
        self._connections: dict[str, list[WsConnection]] = {}
        self._turn_tasks: set[asyncio.Task] = set()
        # prompt_id -> the Future request_human_reply() is waiting on,
        # resolved by whichever connection of the target user sends the
        # matching human_reply frame first (see _handle_frame).
        self._pending_human_replies: dict[str, asyncio.Future[str]] = {}

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
        cap = MAX_CONNECTIONS_PER_ADMIN if role_satisfies(identity.role, "admin") else MAX_CONNECTIONS_PER_USER
        await websocket.accept()
        if len(self._connections.get(username, [])) >= cap:
            logger.info(f"refusing websocket for {username}: already at its cap of {cap}")
            await websocket.close(code=ALREADY_CONNECTED_CLOSE_CODE, reason="Already connected elsewhere.")
            return
        logger.info(f"accepted websocket for {username}")
        connection = WsConnection(websocket)
        Session().connection_id = connection.id
        self._connections.setdefault(username, []).append(connection)
        writer = asyncio.create_task(connection.write_loop())
        try:
            while True:
                raw = await websocket.receive_text()
                self._handle_frame(connection, raw)
        except WebSocketDisconnect:
            pass
        finally:
            remaining = self._connections.get(username)
            if remaining is not None and connection in remaining:
                remaining.remove(connection)
                if not remaining:
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
        elif frame_type == "human_reply":
            self._resolve_human_reply(str(frame.get("prompt_id", "")), str(frame.get("text", "")))
        else:
            logger.debug(f"ignoring an unknown websocket frame type: {frame_type!r}")

    def _resolve_human_reply(self, prompt_id: str, text: str) -> None:
        future = self._pending_human_replies.get(prompt_id)
        if future is not None and not future.done():
            future.set_result(text)

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

    async def push(self, username: str, payload: dict, exclude_connection_id: str | None = None) -> bool:
        """Sends `payload` to every one of `username`'s open connections
        — a dormant/fully-disconnected user just gets False back, no
        exception. `exclude_connection_id` skips one connection (see
        Session.connection_id): used so the tab that triggered a turn
        doesn't also receive its own human_prompt. `async def` only to
        keep every existing `await push(...)` call site unchanged; the
        body itself never actually awaits (WsConnection.send() enqueues
        synchronously)."""
        connections = self._connections.get(username)
        if not connections:
            return False
        sent = False
        for connection in connections:
            if exclude_connection_id is not None and connection.id == exclude_connection_id:
                continue
            connection.send(payload)
            sent = True
        return sent

    async def send_human_prompt(
        self,
        username: str,
        session_id: int,
        prompt_text: str,
        session_type: str | None = None,
        project_id: int | None = None,
        exclude_connection_id: str | None = None,
    ) -> str:
        """The WsHumanRelay.notify() primitive (see talker.human_talker.
        HumanRelay and chat.ws_human_relay.WsHumanRelay): broadcasts a
        human_prompt frame carrying a fresh prompt_id to every one of
        `username`'s connections other than `exclude_connection_id` (the
        tab that just sent the message being answered — it already knows
        what it said, and showing it its own prompt bubble as well is
        confusing rather than informative), and registers that id so a
        matching human_reply resolves await_human_reply() below.
        `session_type`/`project_id` are display-only context for
        whichever tab answers, carried on the frame since answering
        doesn't require navigating there first (see chat.ws_human_relay).
        Returns the prompt_id — the caller must pass it straight to
        await_human_reply(). Raises HumanNotConnectedError if `username`
        has no *other* open connection — nobody could possibly answer."""
        prompt_id = str(uuid.uuid4())
        sent = await self.push(
            username,
            {
                "type": "human_prompt",
                "session_id": session_id,
                "session_type": session_type,
                "project_id": project_id,
                "prompt_id": prompt_id,
                "text": prompt_text,
            },
            exclude_connection_id=exclude_connection_id,
        )
        if not sent:
            raise HumanNotConnectedError(username)
        self._pending_human_replies[prompt_id] = asyncio.get_running_loop().create_future()
        return prompt_id

    async def await_human_reply(self, prompt_id: str) -> str:
        """The WsHumanRelay.receive() primitive: waits for the
        human_reply matching a prompt_id send_human_prompt() returned.
        Raises HumanReplyTimeoutError if none arrives within
        HUMAN_REPLY_TIMEOUT_SECONDS."""
        future = self._pending_human_replies[prompt_id]
        try:
            return await asyncio.wait_for(future, timeout=HUMAN_REPLY_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise HumanReplyTimeoutError(f"No reply for prompt {prompt_id} within {HUMAN_REPLY_TIMEOUT_SECONDS}s.")
        finally:
            self._pending_human_replies.pop(prompt_id, None)
