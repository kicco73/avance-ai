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

from contextvars import ContextVar

# Same placeholder value as db.py's own DEFAULT_USER — kept as an
# independent constant since session.py is meant to be a standalone
# module other layers (including db.py) can depend on, not the reverse.
DEFAULT_USER = "user"

_user: ContextVar[str] = ContextVar("session_user")


class Session(object):
    """Singleton: `Session()` always returns the same instance — only
    what's behind its `user` property is context-scoped, not the
    instance itself. Unset in the current context (e.g. a test, or any
    code path that never went through the auth middleware) reads back as
    DEFAULT_USER, the same fallback this always had."""

    _instance: "Session | None" = None

    def __new__(cls) -> "Session":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def user(self) -> str:
        return _user.get(DEFAULT_USER)

    @user.setter
    def user(self, value: str) -> None:
        _user.set(value)
