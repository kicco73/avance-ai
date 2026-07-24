"""Process-local session state — today just the current user. Not
persisted (see db.py's own Settings table for durable per-user data);
this is where session-scoped attributes live as the app grows past a
single implicit user (e.g. once there's real login, whatever sets the
per-request user would update Session().user here).
"""
from __future__ import annotations

# Same placeholder value as db.py's own DEFAULT_USER for now — kept as
# an independent constant here since session.py is meant to be a
# standalone module other layers (including db.py, eventually) can
# depend on, not the reverse.
DEFAULT_USER = "user"


class Session(object):
    """Singleton: exactly one process-wide session for now (no real
    multi-user support yet — see DEFAULT_USER). `Session()` always
    returns the same instance; its attributes are the actual session
    state, read/written directly by callers (e.g. `Session().user`)."""

    _instance: "Session | None" = None

    def __new__(cls) -> "Session":
        if cls._instance is None:
            instance = super().__new__(cls)
            instance.user = DEFAULT_USER
            cls._instance = instance
        return cls._instance
