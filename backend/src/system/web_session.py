"""The *web* session: request-scoped state for whoever is calling —
who they are, what role they have, and which channel they are speaking
for. It exists only because an HTTP request (or a websocket frame) is
being served, and dies with it.

Not to be confused with db.models.CoreSession, which is a *conversation*
between somebody and an automaton — opened by webchat, by whatsapp, or by
a test, persisted, and outliving any number of requests. The two used to
share the word "session", which made every sentence about one ambiguous.
They touch in exactly one place: a channel's own controller writes
`WebSession().channel` to declare who is speaking, and CoreSession's
admission rules read it (see turn/sessions/session_type_strategy.py).

Durable per-user data (e.g. active_project) lives in the User table
(see db/users.py), not here.

Backed by a ContextVar rather than a plain instance attribute: the app
mixes sync `def` endpoints (run by Starlette in a threadpool) and
`async def` ones, and ai/cascade.py hops onto asyncio.to_thread for
provider calls — a single request's own execution can cross real OS
threads. ContextVar is correctly propagated across run_in_threadpool/
asyncio.to_thread (both copy the current context into the new one);
threading.local would not be — a threadpool worker reused across requests
would leak the previous request's user into the next one.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

_user: ContextVar[str] = ContextVar("session_user")
_role: ContextVar[str] = ContextVar("session_role")
_channel: ContextVar[str] = ContextVar("session_channel")


class WebSession(object):
    """Singleton: `WebSession()` always returns the same instance — only
    what's behind its `user` property is context-scoped, not the
    instance itself. Unset in the current context (e.g. a test, or any
    code path that never went through the auth middleware) raises
    rather than silently resolving to a placeholder user."""

    _instance: "WebSession | None" = None

    def __new__(cls) -> "WebSession":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def user(self) -> str:
        try:
            return _user.get()
        except LookupError as exc:
            raise RuntimeError("WebSession().user accessed outside an authenticated request context.") from exc

    @user.setter
    def user(self, value: str) -> None:
        _user.set(value)

    @property
    def role(self) -> str:
        try:
            return _role.get()
        except LookupError as exc:
            raise RuntimeError("WebSession().role accessed outside an authenticated request context.") from exc

    @role.setter
    def role(self, value: str) -> None:
        _role.set(value)

    @property
    def channel(self) -> str:
        try:
            return _channel.get()
        except LookupError as exc:
            raise RuntimeError("WebSession().channel accessed outside a request context.") from exc

    @channel.setter
    def channel(self, value: str) -> None:
        _channel.set(value)

    @contextmanager
    def impersonate(self, username: str):
        token = _user.set(username)
        try:
            yield
        finally:
            _user.reset(token)

    @contextmanager
    def for_sender(self, username: str, *, role: str, channel: str | None = None):
        """The context a message was sent in, re-entered by whoever is
        handling it.

        A Bus listener does not run inside the request that produced the
        message. It happens to today — bus.publish awaits each listener
        in the publisher's own task, so ContextVars propagate — but that
        is already false for the producers that are not a request at all:
        system/broadcaster.py publishes onto the main loop from another
        thread, and tracking/wakeup_service.py from a scheduled job.
        Both pass `username` on the Message because they had to.

        So a listener establishes its context rather than inheriting it,
        and the Message is where it comes from: `username` and `channel`
        travel in the envelope for exactly this. `role` does not, and
        should not — it is a stored fact, and putting it on the wire
        would let a channel declare its own caller's privileges. The
        caller looks it up and passes it in.

        `channel` is left alone when the message has none, so
        WebSession().channel keeps raising for a caller that never declared
        one (see SessionTypeStrategy.caller_channel) rather than
        answering None and quietly failing a live session's write
        admission instead."""
        tokens = [(_user, _user.set(username)), (_role, _role.set(role))]
        if channel is not None:
            tokens.append((_channel, _channel.set(channel)))
        try:
            yield
        finally:
            for variable, token in reversed(tokens):
                variable.reset(token)
