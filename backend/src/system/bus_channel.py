from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from auth.auth_service import SESSION_COOKIE_NAME, AuthService
from system import bus

from system.bus import (
    CLIENT_INJECTABLE, ENV_BOUND, ENV_CHANGED, ENV_MEMORY_CHANGED, ENV_UNBOUND, OUTPUT_DRIVE, SESSION_EXIT, SESSION_TAKEN_OVER, UI_NOTIFICATION,
    UI_PROGRESS, Message,
)
from auth.roles import role_satisfies
from system.web_session import WebSession

logger = logging.getLogger(__name__)
SWITCHED_TO_OTHER_CLIENT = "switched_to_other_client"
SUPERSEDED_CLOSE_CODE = 4410
MAX_CONNECTIONS_PER_USER = 1
MAX_CONNECTIONS_PER_PRIVILEGED_ROLE = 2
PRIVILEGED_ROLE_FLOOR = "customer"
HUMAN_REPLY_TIMEOUT_SECONDS = 300.0
WEB_FORWARDED = (UI_NOTIFICATION, SESSION_TAKEN_OVER, UI_PROGRESS, OUTPUT_DRIVE, ENV_CHANGED, ENV_MEMORY_CHANGED)
HUMAN_PROMPT = "human_prompt"
ENV_BINDINGS = "env.bindings"
_ENVELOPE = frozenset({"type", "session_id", "project_id"})
CLIENT_REGISTRABLE = WEB_FORWARDED + (HUMAN_PROMPT,)


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

    def __init__(self, websocket: WebSocket, username: str) -> None:
        self.id = str(uuid.uuid4())
        self.username = username
        self._websocket = websocket
        self._outgoing: asyncio.Queue[dict | None] = asyncio.Queue()
        self._closed = False
        self._close_code: int | None = None
        self._subscriptions: set[str] = set()
        self._watching: set[int] = set()

    def subscribe(self, event_types: list[str]) -> None:
        self._subscriptions.update(event_types)

    def unsubscribe(self, event_types: list[str]) -> None:
        self._subscriptions.difference_update(event_types)

    def wants(self, event_type: str) -> bool:
        return event_type in self._subscriptions

    def watch(self, session_id: int) -> None:
        self._watching.add(session_id)

    def unwatch(self, session_id: int) -> None:
        self._watching.discard(session_id)

    def watched(self) -> tuple[int, ...]:
        return tuple(self._watching)

    @property
    def closed(self) -> bool:
        """True once this socket stopped serving — it was superseded, or
        its own loop ended. An inbound frame arriving after that is read
        off a socket already on its way out and is not acted on (see
        BusChannel._handle_frame)."""
        return self._closed

    def send(self, payload: dict) -> None:
        if self._closed:
            logger.debug(f"websocket gone, frame discarded: type={payload.get('type')} turn_id={payload.get('turn_id')}")
            return
        self._outgoing.put_nowait(payload)

    async def write_loop(self) -> None:
        """One frame failing to send — a transient network blip, a
        payload that momentarily can't serialize — must not take every
        frame queued after it down too: the browser hasn't disconnected
        (that surfaces on the *receive* side, as WebSocketDisconnect,
        which is what actually calls close() below), so there is no
        reason yet to stop trying. Logged at WARNING, not swallowed at
        DEBUG: a frame that never reached the browser is worth knowing
        about, not just discarding quietly."""
        while True:
            payload = await self._outgoing.get()
            if payload is None:
                await self._close_socket()
                return
            try:
                await self._websocket.send_json(payload)
            except Exception as exc:
                logger.warning(f"websocket send failed, frame dropped: type={payload.get('type')}: {exc}")

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


class HumanPrompt(object):
    __slots__ = ("id", "session_id", "operator", "frame", "_reply", "_typing")

    def __init__(self, prompt_id: str, session_id: int, operator: str, frame: dict) -> None:
        self.id = prompt_id
        self.session_id = session_id
        self.operator = operator
        self.frame = frame
        self._reply: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._typing = asyncio.Event()

    @property
    def answered(self) -> bool:
        return self._reply.done()

    @property
    def typing_seen(self) -> bool:
        return self._typing.is_set()

    def answer(self, text: str) -> None:
        if not self._reply.done():
            self._reply.set_result(text)

    def typing_started(self) -> None:
        self._typing.set()

    async def reply(self) -> str:
        return await asyncio.wait_for(self._reply, timeout=HUMAN_REPLY_TIMEOUT_SECONDS)

    async def typing(self) -> None:
        await self._typing.wait()


class BusChannel(object):
    """Every websocket one identity holds open, both directions: the
    browser sends `turn` frames on it (the only inbound chat frame —
    actions, session bootstrap and everything else stay HTTP) and
    receives every frame the server has for it — a turn's own chunk/
    tool/done/error, each carrying the turn_id of the `turn` frame that
    produced it, plus the notification/progress/system_warning/
    human_prompt frames, each going only to the connections that
    registered for it (see push_event and CLIENT_REGISTRABLE).

    Ordering guarantee: one receive loop per socket reads `turn` frames
    in arrival order, and each user message is persisted right there, in
    reading order, before any processing starts. The turn itself then
    runs as a task; the session lock serializes turns of one session.

    At most MAX_CONNECTIONS_PER_USER connections per identity
    (MAX_CONNECTIONS_PER_PRIVILEGED_ROLE for anything ranked "customer" or
    above — staff, not a plain end user), and the newest always wins: a
    connection past the cap is accepted, and the oldest is superseded to
    make room (see _supersede_over_cap). The tab that loses the channel
    is never left to go quiet guessing why — it is told first, with a
    SWITCHED_TO_OTHER_CLIENT frame it blocks its own chat on. The higher
    cap is what lets a privileged tab open a preview/test chat in a
    second connection (App Store, Manage Projects, the project editor's
    Run tab) without superseding its own primary one."""

    def __init__(self, auth_service: AuthService) -> None:
        self.__auth_service = auth_service
        self.__channel: str | None = None
        for message_type in WEB_FORWARDED:
            bus.subscribe(message_type, self.__forward_to_web)
        bus.subscribe(ENV_BOUND, self.__bound)
        bus.subscribe(ENV_UNBOUND, self.__unbound)
        bus.subscribe(ENV_CHANGED, self.__bound_value_changed)
        self.__env_bindings: dict[int, dict[str, Any]] = {}
        self._connections: dict[str, list[WsConnection]] = {}
        self.__by_id: dict[str, WsConnection] = {}
        self.__watchers: dict[int, set[WsConnection]] = {}
        self.__inbound_tasks: set[asyncio.Task] = set()
        self.__prompts: dict[str, HumanPrompt] = {}
        self.__prompt_by_session: dict[int, HumanPrompt] = {}
        self.__prompts_by_operator: dict[str, dict[str, HumanPrompt]] = {}

    async def channel_loop(self, websocket: WebSocket) -> None:
        token = websocket.cookies.get(SESSION_COOKIE_NAME)
        identity = self.__auth_service.verify_token(token) if token else None
        if identity is None or identity.role is None:
            await websocket.close(code=4401)
            return
        WebSession().user = identity.email
        WebSession().role = identity.role

        username = WebSession().user
        cap = (
            MAX_CONNECTIONS_PER_PRIVILEGED_ROLE if role_satisfies(identity.role, PRIVILEGED_ROLE_FLOOR)
            else MAX_CONNECTIONS_PER_USER
        )
        await websocket.accept()
        logger.info(f"accepted websocket for {username}")
        connection = WsConnection(websocket, username)
        self.__register(connection)
        self.__supersede_over_cap(username, cap)
        writer = asyncio.create_task(connection.write_loop())
        try:
            while True:
                raw = await websocket.receive_text()
                self.__handle_frame(connection, raw)
        except WebSocketDisconnect:
            pass
        finally:
            self.__deregister(connection)
            connection.close()
            await writer

    def __register(self, connection: WsConnection) -> None:
        self._connections.setdefault(connection.username, []).append(connection)
        self.__by_id[connection.id] = connection

    def __deregister(self, connection: WsConnection) -> None:
        remaining = self._connections.get(connection.username)
        if remaining is not None and connection in remaining:
            remaining.remove(connection)
            if not remaining:
                del self._connections[connection.username]
        self.__forget_connection(connection)

    def __forget_connection(self, connection: WsConnection) -> None:
        self.__by_id.pop(connection.id, None)
        for session_id in connection.watched():
            self.__unwatch(connection, session_id)

    def __supersede_over_cap(self, username: str, cap: int) -> None:
        """Makes room for the connection that just arrived by dropping the
        oldest ones, rather than refusing the newcomer: the browser the
        person is looking at is always the one that just connected, and a
        cap that turned it away left them staring at a chat they could not
        use until they hunted down a tab they may no longer have open."""
        connections = self._connections.get(username, [])
        while len(connections) > cap:
            superseded = connections.pop(0)
            self.__forget_connection(superseded)
            logger.info(f"superseding an older websocket of {username}: a newer client took the channel")
            superseded.supersede()

    def __handle_frame(self, connection: WsConnection, raw: str) -> None:
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
            self.__publish_client_frame(connection, frame_type, frame)
        elif frame_type == "subscribe":
            registered = self.__registrable(frame.get("events"))
            connection.subscribe(registered)
            self.__deliver_pending_prompts(connection, registered)
        elif frame_type == "unsubscribe":
            connection.unsubscribe(self.__registrable(frame.get("events")))
        elif frame_type == SESSION_EXIT:
            for session_id in filter(None, [frame.get("session_id")]):
                self.__unwatch(connection, session_id)
        elif frame_type == "human_reply":
            self.__resolve_human_reply_for_session(connection, frame.get("session_id"), str(frame.get("text", "")))
        elif frame_type == "human_typing":
            self.__notify_typing_for_session(connection, frame.get("session_id"))
        else:
            logger.debug(f"ignoring an unknown websocket frame type: {frame_type!r}")

    def __registrable(self, events) -> list[str]:
        """The subset of what a client asked for that it is allowed to
        register for. Anything else is refused rather than registered: a
        client naming an internal type would otherwise turn a
        registration into a way to read one."""
        if not isinstance(events, list):
            return []
        registrable: list[str] = []
        refused: list[str] = []
        for event in events:
            if isinstance(event, str):
                (registrable if event in CLIENT_REGISTRABLE else refused).append(event)
        if refused:
            logger.warning(f"refusing a websocket registration for types a client may not have: {refused}")
        return registrable

    def __deliver_pending_prompts(self, connection: WsConnection, registered: list[str]) -> None:
        """A prompt that fired before this connection registered. The push
        is one-shot and the operator's view opens from a takeover
        notification, so the prompt it has to answer is usually already
        waiting by the time it asks for them."""
        if HUMAN_PROMPT not in registered:
            return
        for prompt in list(self.__prompts_by_operator.get(connection.username, {}).values()):
            connection.send(prompt.frame)

    def __prompt_awaiting(self, connection: WsConnection, session_id) -> HumanPrompt | None:
        """The prompt this session is waiting on, but only when it is
        this very connection's identity that was asked: the operator's
        own frames carry session_id, which any other signed-in user
        could name just as well."""
        prompt = self.__prompt_by_session.get(session_id)
        if prompt is None:
            return None
        if prompt.operator != connection.username:
            logger.warning(
                "ignoring a human frame for session %s from %s: %s was the one asked.",
                session_id, connection.username, prompt.operator,
            )
            return None
        return prompt

    def __resolve_human_reply_for_session(self, connection: WsConnection, session_id, text: str) -> None:
        for prompt in filter(None, [self.__prompt_awaiting(connection, session_id)]):
            prompt.answer(text)

    def __notify_typing_for_session(self, connection: WsConnection, session_id) -> None:
        for prompt in filter(None, [self.__prompt_awaiting(connection, session_id)]):
            prompt.typing_started()

    def __publish_client_frame(self, connection: WsConnection, frame_type: str, frame: dict) -> None:
        """One inbound frame, onto the Bus. `origin_id` carries the
        connection it arrived on so whoever answers can answer *there*
        (see send_to_connection) rather than to every tab this identity
        has open.

        The channel is whatever the interface listening here told this
        socket it was (see owned_by) — this package still does not know
        which interface that is, and still names no channel of its own.
        It stamps one because a listener is not the publisher's own call
        stack: whoever answers this message may be anywhere, and cannot
        recognise a connection it does not own."""
        message = Message(
            type=frame_type,
            body={key: value for key, value in frame.items() if key not in _ENVELOPE},
            username=WebSession().user,
            session_id=frame.get("session_id"),
            project_id=frame.get("project_id"),
            channel=self.__channel,
            origin_id=connection.id,
        )
        task = asyncio.create_task(bus.publish(message))
        self.__inbound_tasks.add(task)
        task.add_done_callback(self.__inbound_tasks.discard)

    def owned_by(self, channel: str) -> None:
        """Claimed by the interface that listens here, at boot, naming
        the channel it speaks on — the one thing about itself this socket
        cannot work out. One claim, because one interface owns the
        browser's connection: a second would not mean two channels
        sharing a socket, it would mean a build with two chat windows.

        Left unclaimed the socket still carries frames; they just arrive
        with no channel, which is what a build without the package that
        answers them should look like."""
        self.__channel = channel

    def has_connection(self, connection_id: str) -> bool:
        """Whether `connection_id` is one of the connections open here.
        What a listener asks to tell a frame that came in over this
        socket from one that reached the Bus some other way — an
        `input.text` converted from a voice note carries the id of the
        message it was converted from, not of a connection."""
        return connection_id in self.__by_id

    def watch_session(self, connection_id: str, session_id: int) -> None:
        """That connection is showing that conversation, from now until
        it says otherwise. Told by whoever answered its `session.enter`
        (see webchat_service), because the socket never learns which
        session a request resolved to."""
        for connection in filter(None, [self.__by_id.get(connection_id)]):
            self.__watch(connection, session_id)
            for values in filter(None, [self.__env_bindings.get(session_id)]):
                connection.send(self.__bindings_frame(session_id, values))

    def unwatch_session(self, session_id: int) -> None:
        self.__env_bindings.pop(session_id, None)
        for connection in self.__watchers.pop(session_id, ()):
            connection.unwatch(session_id)

    async def __bound(self, message: Message) -> None:
        body = message.body or {}
        for session_id in filter(None, [message.session_id]):
            self.__env_bindings.setdefault(session_id, {})[body["key"]] = body.get("value")
            self.send_to_watchers(session_id, self.__bindings_frame(session_id, self.__env_bindings[session_id]))

    async def __unbound(self, message: Message) -> None:
        for session_id in filter(None, [message.session_id]):
            self.__env_bindings.pop(session_id, None)
            self.send_to_watchers(session_id, self.__bindings_frame(session_id, {}))

    async def __bound_value_changed(self, message: Message) -> None:
        body = message.body or {}
        bindings = self.__env_bindings.get(message.session_id, {})
        for key in filter(bindings.__contains__, [body.get("key")]):
            bindings[key] = body.get("value")
            self.send_to_watchers(message.session_id, self.__bindings_frame(message.session_id, bindings))

    @staticmethod
    def __bindings_frame(session_id: int, values: dict[str, Any]) -> dict:
        return {"type": ENV_BINDINGS, "session_id": session_id, "values": dict(values)}

    def __watch(self, connection: WsConnection, session_id: int) -> None:
        connection.watch(session_id)
        self.__watchers.setdefault(session_id, set()).add(connection)

    def __unwatch(self, connection: WsConnection, session_id: int) -> None:
        connection.unwatch(session_id)
        watchers = self.__watchers.get(session_id)
        if watchers is not None:
            watchers.discard(connection)
            if not watchers:
                del self.__watchers[session_id]

    def send_to_watchers(self, session_id: int, payload: dict) -> int:
        """One frame to every connection showing that conversation, and
        the count of them. For what nobody asked for: a session closed
        from somewhere else, an operator taking one over. Answering a
        request goes to the connection that made it instead (see
        send_to_connection)."""
        watchers = list(self.__watchers.get(session_id, ()))
        for connection in watchers:
            connection.send(payload)
        return len(watchers)

    def send_to_connection(self, connection_id: str, payload: dict) -> bool:
        """Writes one frame to one open connection, by the id an inbound
        message carried in `origin_id`. False when that connection is
        gone — the caller is streaming, and a closed tab is an ordinary
        outcome, not an error."""
        connection = self.__by_id.get(connection_id)
        if connection is None:
            return False
        connection.send(payload)
        return True

    async def __forward_to_web(self, message: Message) -> None:
        """Every WEB_FORWARDED message, to that identity's connections
        that registered for its type, under that same type. The socket is
        this object's business, and a producer never learns whether
        anyone was connected, let alone subscribed (see
        bus.UI_NOTIFICATION)."""
        for username in filter(None, [message.username]):
            frame = {"type": message.type, **(message.body or {})}
            for session_id in filter(None, [message.session_id]):
                frame["session_id"] = session_id
            for project_id in filter(None, [message.project_id]):
                frame["project_id"] = project_id
            await self.push_event(username, message.type, frame)

    async def push_event(self, username: str, event_type: str, payload: dict) -> bool:
        """One Bus event, to `username`'s connections that registered for
        it (see WsConnection.wants) — never to a connection that merely
        exists. False means nobody was listening for it, which is an
        ordinary outcome, not an error."""
        return self.__send_to(
            [connection for connection in self._connections.get(username, ()) if connection.wants(event_type)],
            payload,
        )

    def __send_to(self, connections: list[WsConnection], payload: dict) -> bool:
        for connection in connections:
            connection.send(payload)
        return bool(connections)

    async def send_human_prompt(
        self,
        username: str,
        session_id: int,
        prompt_text: str,
        session_type: str | None = None,
        project_id: int | None = None,
    ) -> str:
        """The BusHumanRelay.notify() primitive (see talker.human_talker.
        HumanRelay and system.bus_human_relay.BusHumanRelay): sends a
        human_prompt frame carrying a fresh prompt_id to `username`'s
        connections that registered for that type, and registers that id
        so a matching human_reply resolves await_human_reply() below.
        `session_type`/`project_id` are display-only context for
        whichever tab answers, carried on the frame since answering
        doesn't require navigating there first (see system.bus_human_relay).
        Returns the prompt_id — the caller must pass it straight to
        await_human_reply()/wait_for_typing(). Raises HumanNotConnectedError
        when no connection asked for these: nobody there is answering as a
        person, whatever else they may have open."""
        prompt_id = str(uuid.uuid4())
        frame = {
            "type": HUMAN_PROMPT,
            "session_id": session_id,
            "session_type": session_type,
            "project_id": project_id,
            "prompt_id": prompt_id,
            "text": prompt_text,
        }
        if not await self.push_event(username, HUMAN_PROMPT, frame):
            raise HumanNotConnectedError(username)
        prompt = HumanPrompt(prompt_id, session_id, username, frame)
        self.__prompts[prompt_id] = prompt
        self.__prompt_by_session[session_id] = prompt
        self.__prompts_by_operator.setdefault(username, {})[prompt_id] = prompt
        return prompt_id

    async def await_human_reply(self, prompt_id: str) -> str:
        """The BusHumanRelay.receive() primitive: waits for the
        human_reply matching a prompt_id send_human_prompt() returned —
        the operator's own frame carries session_id, not this prompt_id
        (see _resolve_human_reply_for_session), so this is purely an
        internal correlation key. Raises HumanReplyTimeoutError if none
        arrives within HUMAN_REPLY_TIMEOUT_SECONDS."""
        prompt = self.__prompts[prompt_id]
        try:
            return await prompt.reply()
        except asyncio.TimeoutError:
            raise HumanReplyTimeoutError(f"No reply for prompt {prompt_id} within {HUMAN_REPLY_TIMEOUT_SECONDS}s.")
        finally:
            self.__forget_prompt(prompt)

    def __forget_prompt(self, prompt: HumanPrompt) -> None:
        self.__prompts.pop(prompt.id, None)
        by_operator = self.__prompts_by_operator.get(prompt.operator)
        if by_operator is not None:
            by_operator.pop(prompt.id, None)
            if not by_operator:
                del self.__prompts_by_operator[prompt.operator]
        if self.__prompt_by_session.get(prompt.session_id) is prompt:
            del self.__prompt_by_session[prompt.session_id]

    async def wait_for_typing(self, prompt_id: str) -> None:
        """The BusHumanRelay.wait_for_typing() primitive: resolves the
        instant the operator's own human_typing frame arrives for this
        prompt's session (see _notify_typing_for_session) — HumanTalker.
        chat() races this against await_human_reply() so a reply that
        beats it to the operator's own keystroke never shows a typing
        signal at all."""
        await self.__prompts[prompt_id].typing()
