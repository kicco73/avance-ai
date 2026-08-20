"""Process-local session state — today just the current user, not
persisted (see db.py's Settings table for durable per-user data).
Session-scoped attributes belong here as the app grows past one implicit user.
"""
from __future__ import annotations

# Same placeholder value as db.py's own DEFAULT_USER — kept as an
# independent constant since session.py is meant to be a standalone
# module other layers (including db.py) can depend on, not the reverse.
DEFAULT_USER = "user"


class Session(object):
    """Singleton: exactly one process-wide session (no real multi-user
    support yet). `Session()` always returns the same instance; its
    attributes are read/written directly by callers (e.g. `Session().user`)."""

    _instance: "Session | None" = None
    user : str

    def __new__(cls) -> "Session":
        if cls._instance is None:
            instance = super().__new__(cls)
            instance.user = DEFAULT_USER
            cls._instance = instance
        return cls._instance
