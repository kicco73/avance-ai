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

    @contextmanager
    def impersonate(self, username: str):
        token = _user.set(username)
        try:
            yield
        finally:
            _user.reset(token)
