from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import WebSocket, WebSocketDisconnect

from auth.auth_service import SESSION_COOKIE_NAME, AuthService
from system import bus

from system.bus import CLIENT_INJECTABLE, UI_HUMAN_TAKEOVER, UI_NOTIFICATION, UI_SYSTEM_WARNING, UI_PROGRESS, Message
from auth.roles import role_satisfies
from system.session import Session
from turn.channels import NATIVE_CHAT

logger = logging.getLogger(__name__)

# The frame an older connection is told it lost the channel with, and the
# close code that follows it. The frontend must treat that code
# specially: never reconnect (the newest client owns the channel now),
# block the chat instead (see chatChannel.js). 44xx is our own
# application range (4401 is the existing auth-failure code).
SWITCHED_TO_OTHER_CLIENT = "switched_to_other_client"
SUPERSEDED_CLOSE_CODE = 4410

# How many concurrent sockets one identity may hold: enough for one tab,
# or for an admin testing HumanTalker to answer their own session from a
# second tab (see talker.human_talker) — not "multi-device support", so
# deliberately small. The newest connection always wins: a connection
# past the cap is accepted and the oldest is superseded instead (see
# WsConnection.supersede), because the browser that just asked for the
# chat is the one the person is actually looking at.
MAX_CONNECTIONS_PER_USER = 1
MAX_CONNECTIONS_PER_ADMIN = 2

# How long request_human_reply() waits for any of the user's connections
# to answer before giving up — this is a manual-testing seam (see
# talker.human_talker.HumanTalker), not a production SLA.
HUMAN_REPLY_TIMEOUT_SECONDS = 300.0

# What this socket is allowed to carry out of the Bus, and the whole of
# the translation it does on the way: none. A frame reaching the browser
# is the message that was published, under its own type — this object is
# the Bus's reach into a web client, with a filter on what may leave, not
# a second vocabulary (see docs/BUS.md).
WEB_FORWARDED = (UI_NOTIFICATION, UI_HUMAN_TAKEOVER, UI_SYSTEM_WARNING, UI_PROGRESS)


class HumanReplyTimeoutError(Exception):
    """No connection of the target user answered a human_prompt within
    HUMAN_REPLY_TIMEOUT_SECONDS."""


class HumanNotConnectedError(Exception):
    """The target user has no open websocket at all — nothing to
    broadcast a human_prompt to."""

    def __init__(self, username: str) -> None:
        super().__init__(f"{username} is not currently connected.")


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
        self._close_code: int | None = None

    @property
    def closed(self) -> bool:
        """True once this socket stopped serving — it was superseded, or
        its own loop ended. An inbound frame arriving after that is read
        off a socket already on its way out and is not acted on (see
        WsNotifications._handle_frame)."""
        return self._closed

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
                    await self._close_socket()
                    return
                await self._websocket.send_json(payload)
        except Exception as exc:
            logger.debug(f"websocket writer stopped: {exc}")
        finally:
            self._closed = True

    async def _close_socket(self) -> None:
        """Closing goes through the writer task rather than the caller so
        it lands *after* whatever was already queued — supersede() below
        depends on its frame reaching the wire before the close does."""
        if self._close_code is None:
            return
        try:
            await self._websocket.close(code=self._close_code)
        except Exception as exc:
            logger.debug(f"websocket already gone when closing: {exc}")

    def close(self, code: int | None = None) -> None:
        self._close_code = code
        self._closed = True
        self._outgoing.put_nowait(None)

    def supersede(self) -> None:
        """Another client of this same identity just took the channel
        over. This socket is told so — the frame is what the browser
        blocks its chat on — and then closed with SUPERSEDED_CLOSE_CODE,
        which is the frontend's cue not to reconnect and steal it back."""
        self.send({"type": SWITCHED_TO_OTHER_CLIENT})
        self.close(code=SUPERSEDED_CLOSE_CODE)


class WsNotifications(object):
    """Every websocket one identity holds open, both directions: the
    browser sends `turn` frames on it (the only inbound chat frame —
    actions, session bootstrap and everything else stay HTTP) and
    receives every frame the server has for it — a turn's own chunk/
    tool/done/error, each carrying the turn_id of the `turn` frame that
    produced it, plus the push-only notification/progress/
    system_warning/human_prompt frames, sent to every one of that
    identity's own connections at once (see push()).

    Ordering guarantee: one receive loop per socket reads `turn` frames
    in arrival order, and each user message is persisted right there, in
    reading order, before any processing starts. The turn itself then
    runs as a task; the session lock serializes turns of one session.

    At most MAX_CONNECTIONS_PER_USER connections per identity
    (MAX_CONNECTIONS_PER_ADMIN for an admin), and the newest always wins:
    a connection past the cap is accepted, and the oldest is superseded
    to make room (see _supersede_over_cap). The tab that loses the
    channel is never left to go quiet guessing why — it is told first,
    with a SWITCHED_TO_OTHER_CLIENT frame it blocks its own chat on."""

    def __init__(self, auth_service: AuthService) -> None:
        self._auth_service = auth_service
        # The only thing in the process that writes to these sockets, and
        # so the only thing that subscribes on their behalf: a producer
        # publishes a nudge and never holds a connection (see
        # _forward_notification).
        for message_type in WEB_FORWARDED:
            bus.subscribe(message_type, self._forward_to_web)
        # username -> every open connection of that identity, oldest
        # first — see the class docstring for the cap.
        self._connections: dict[str, list[WsConnection]] = {}
        self._inbound_tasks: set[asyncio.Task] = set()
        # prompt_id -> the Future await_human_reply() is waiting on, and
        # the Event wait_for_typing() is waiting on — one pair per prompt,
        # both resolved by session_id (see _current_prompt_for_session):
        # the operator's own frame never needs to know the prompt_id
        # itself, only which session it's answering.
        self._pending_human_replies: dict[str, asyncio.Future[str]] = {}
        self._pending_typing_events: dict[str, asyncio.Event] = {}
        # session_id -> the one prompt currently open for it (at most one
        # at a time — HumanTalker.chat() awaits a reply before asking
        # again) — what lets the operator's human_reply/human_typing
        # frames carry session_id instead of a prompt_id they may never
        # have seen (see send_human_prompt's own docstring).
        self._current_prompt_for_session: dict[int, str] = {}
        self._session_for_prompt: dict[str, int] = {}
        # prompt_id -> the identity that prompt was actually sent to. A
        # reply is only a reply when it comes from them: without this,
        # anyone who guessed a session_id awaiting a human could answer
        # in their place.
        self._operator_for_prompt: dict[str, str] = {}

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
        logger.info(f"accepted websocket for {username}")
        connection = WsConnection(websocket)
        Session().connection_id = connection.id
        self._connections.setdefault(username, []).append(connection)
        self._supersede_over_cap(username, cap)
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

    def _supersede_over_cap(self, username: str, cap: int) -> None:
        """Makes room for the connection that just arrived by dropping the
        oldest ones, rather than refusing the newcomer: the browser the
        person is looking at is always the one that just connected, and a
        cap that turned it away left them staring at a chat they could not
        use until they hunted down a tab they may no longer have open."""
        connections = self._connections.get(username, [])
        while len(connections) > cap:
            superseded = connections.pop(0)
            logger.info(f"superseding an older websocket of {username}: a newer client took the channel")
            superseded.supersede()

    def _handle_frame(self, connection: WsConnection, raw: str) -> None:
        # A superseded socket may still deliver whatever was in flight
        # when a newer client took the channel over — it is no longer
        # this identity's chat, so nothing it says is acted on.
        if connection.closed:
            logger.debug("ignoring a frame from a superseded websocket")
            return
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
        elif frame_type in CLIENT_INJECTABLE:
            # A client speaks as a person: the only types it may put on
            # the Bus are the ones a person can say (see bus.py's own
            # CLIENT_INJECTABLE). What happens next is not this object's
            # business — it publishes and stops. With no listener the
            # frame is simply not answered, which is what a build with no
            # chat installed looks like from here.
            self._publish_client_frame(connection, frame_type, frame)
        elif frame_type == "human_reply":
            self._resolve_human_reply_for_session(connection, frame.get("session_id"), str(frame.get("text", "")))
        elif frame_type == "human_typing":
            self._notify_typing_for_session(connection, frame.get("session_id"))
        else:
            logger.debug(f"ignoring an unknown websocket frame type: {frame_type!r}")

    def _username_of(self, connection: WsConnection) -> str | None:
        for username, connections in self._connections.items():
            if connection in connections:
                return username
        return None

    def _prompt_awaiting(self, connection: WsConnection, session_id) -> str | None:
        """The prompt this session is waiting on, but only when it is
        this very connection's identity that was asked: the operator's
        own frames carry session_id, which any other signed-in user
        could name just as well."""
        prompt_id = self._current_prompt_for_session.get(session_id)
        if prompt_id is None:
            return None
        operator = self._operator_for_prompt.get(prompt_id)
        sender = self._username_of(connection)
        if operator != sender:
            logger.warning(
                "ignoring a human frame for session %s from %s: %s was the one asked.",
                session_id, sender, operator,
            )
            return None
        return prompt_id

    def _resolve_human_reply_for_session(self, connection: WsConnection, session_id, text: str) -> None:
        prompt_id = self._prompt_awaiting(connection, session_id)
        if prompt_id is None:
            return
        future = self._pending_human_replies.get(prompt_id)
        if future is not None and not future.done():
            future.set_result(text)

    def _notify_typing_for_session(self, connection: WsConnection, session_id) -> None:
        prompt_id = self._prompt_awaiting(connection, session_id)
        if prompt_id is None:
            return
        event = self._pending_typing_events.get(prompt_id)
        if event is not None:
            event.set()

    def _publish_client_frame(self, connection: WsConnection, frame_type: str, frame: dict) -> None:
        """One inbound frame, onto the Bus. `origin_id` carries the
        connection it arrived on so whoever answers can answer *there*
        (see send_to_connection) rather than to every tab this identity
        has open, and `stream_id` names the one exchange over it."""
        message = Message(
            type=frame_type,
            body=str(frame.get("body", "")),
            username=Session().user,
            session_id=frame.get("session_id"),
            channel=NATIVE_CHAT,
            origin_id=connection.id,
            stream_id=str(frame.get("stream_id", "")),
        )
        task = asyncio.create_task(bus.publish(message))
        self._inbound_tasks.add(task)
        task.add_done_callback(self._inbound_tasks.discard)

    def send_to_connection(self, connection_id: str, payload: dict) -> bool:
        """Writes one frame to one open connection, by the id an inbound
        message carried in `origin_id`. False when that connection is
        gone — the caller is streaming, and a closed tab is an ordinary
        outcome, not an error."""
        for connections in self._connections.values():
            for connection in connections:
                if connection.id == connection_id:
                    connection.send(payload)
                    return True
        return False

    async def _forward_to_web(self, message: Message) -> None:
        """Every WEB_FORWARDED message, to that identity's open
        connections, under its own type. The socket is this object's
        business, and a producer never learns whether anyone was
        connected (see bus.UI_NOTIFICATION)."""
        for username in filter(None, [message.username]):
            await self.push(username, {"type": message.type, **(message.body or {})})

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
        HumanRelay and system.ws_human_relay.WsHumanRelay): broadcasts a
        human_prompt frame carrying a fresh prompt_id to every one of
        `username`'s connections other than `exclude_connection_id` (the
        tab that just sent the message being answered — it already knows
        what it said, and showing it its own prompt bubble as well is
        confusing rather than informative), and registers that id so a
        matching human_reply resolves await_human_reply() below.
        `session_type`/`project_id` are display-only context for
        whichever tab answers, carried on the frame since answering
        doesn't require navigating there first (see system.ws_human_relay).
        Returns the prompt_id — the caller must pass it straight to
        await_human_reply()/wait_for_typing(). Raises HumanNotConnectedError
        if `username` has no *other* open connection — nobody could
        possibly answer."""
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
        self._pending_typing_events[prompt_id] = asyncio.Event()
        self._current_prompt_for_session[session_id] = prompt_id
        self._session_for_prompt[prompt_id] = session_id
        self._operator_for_prompt[prompt_id] = username
        return prompt_id

    async def send_human_takeover(self, username: str, session_id: int, project_id: str) -> None:
        """chat.switch_to_human(user_id)'s own push (see
        tracking.actuators.chat_namespace.LiveChatNamespace.switch_to_human):
        tells every one of `username`'s open connections that session_id
        needs a human now, carrying enough to open it (project_id) —
        never `exclude_connection_id`, since both of the operator's own
        connections are the ones being paged, not whatever
        triggered the call. Best-effort like push(): a dormant/
        offline operator just doesn't get it live, same as any other
        push — get_human_operator(session_id) is queryable state, not a
        one-shot event, so they still see it once they open the session."""
        await self.push(username, {"type": UI_HUMAN_TAKEOVER, "session_id": session_id, "project_id": project_id})

    async def await_human_reply(self, prompt_id: str) -> str:
        """The WsHumanRelay.receive() primitive: waits for the
        human_reply matching a prompt_id send_human_prompt() returned —
        the operator's own frame carries session_id, not this prompt_id
        (see _resolve_human_reply_for_session), so this is purely an
        internal correlation key. Raises HumanReplyTimeoutError if none
        arrives within HUMAN_REPLY_TIMEOUT_SECONDS."""
        future = self._pending_human_replies[prompt_id]
        try:
            return await asyncio.wait_for(future, timeout=HUMAN_REPLY_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise HumanReplyTimeoutError(f"No reply for prompt {prompt_id} within {HUMAN_REPLY_TIMEOUT_SECONDS}s.")
        finally:
            self._pending_human_replies.pop(prompt_id, None)
            self._pending_typing_events.pop(prompt_id, None)
            self._operator_for_prompt.pop(prompt_id, None)
            session_id = self._session_for_prompt.pop(prompt_id, None)
            if session_id is not None and self._current_prompt_for_session.get(session_id) == prompt_id:
                del self._current_prompt_for_session[session_id]

    async def wait_for_typing(self, prompt_id: str) -> None:
        """The WsHumanRelay.wait_for_typing() primitive: resolves the
        instant the operator's own human_typing frame arrives for this
        prompt's session (see _notify_typing_for_session) — HumanTalker.
        chat() races this against await_human_reply() so a reply that
        beats it to the operator's own keystroke never shows a typing
        signal at all."""
        event = self._pending_typing_events[prompt_id]
        await event.wait()
