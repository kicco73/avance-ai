"""Request-scoped session state — today just the current user. Durable
per-user data (e.g. active_project) lives in db.py's User table (see
db/users.py), not here.

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
_connection_id: ContextVar[str] = ContextVar("session_connection_id")


class Session(object):
    """Singleton: `Session()` always returns the same instance — only
    what's behind its `user` property is context-scoped, not the
    instance itself. Unset in the current context (e.g. a test, or any
    code path that never went through the auth middleware) raises
    rather than silently resolving to a placeholder user."""

    _instance: "Session | None" = None

    def __new__(cls) -> "Session":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def user(self) -> str:
        try:
            return _user.get()
        except LookupError as exc:
            raise RuntimeError("Session().user accessed outside an authenticated request context.") from exc

    @user.setter
    def user(self, value: str) -> None:
        _user.set(value)

    @property
    def role(self) -> str:
        try:
            return _role.get()
        except LookupError as exc:
            raise RuntimeError("Session().role accessed outside an authenticated request context.") from exc

    @role.setter
    def role(self, value: str) -> None:
        _role.set(value)

    @property
    def channel(self) -> str:
        try:
            return _channel.get()
        except LookupError as exc:
            raise RuntimeError("Session().channel accessed outside a request context.") from exc

    @channel.setter
    def channel(self, value: str) -> None:
        _channel.set(value)

    @property
    def connection_id(self) -> str | None:
        """Which WsConnection (see chat.ws_notifications) the current
        context is running on, if any. Unlike user/role/channel this is
        lenient — most request contexts (HTTP, tests, WhatsApp) never go
        through a websocket at all, and have no connection to identify.
        It exists solely so a human_prompt broadcast (see
        chat.ws_human_relay.WsHumanRelay) can best-effort exclude the tab
        that triggered the turn from also seeing its own prompt."""
        return _connection_id.get(None)

    @connection_id.setter
    def connection_id(self, value: str | None) -> None:
        _connection_id.set(value)

    @contextmanager
    def impersonate(self, username: str):
        token = _user.set(username)
        try:
            yield
        finally:
            _user.reset(token)
