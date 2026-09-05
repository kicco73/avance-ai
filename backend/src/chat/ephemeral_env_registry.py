"""In-memory Env store for test/preview sessions, one per session id —
never backed by the database (see chat.env_for_session's own docstring
for why). A process-wide singleton, same shape as session.Session: every
caller that resolves a test/preview session's own env — ChatService,
TrackingService, ScopeHydrator, a testing/ replay — must see the same
instance for a given session id, or a chat turn's own env write would be
invisible to whoever reads it back (the Inspector, a later turn in the
same session, ...).
"""
from __future__ import annotations

from tracking.env import Env


class EphemeralEnvRegistry(object):
    _instance: "EphemeralEnvRegistry | None" = None

    def __new__(cls) -> "EphemeralEnvRegistry":
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._envs = {}
            cls._instance = instance
        return cls._instance

    def get(self, session_id: int) -> Env:
        """Creates an empty Env the first time `session_id` is seen —
        which is also what a "reset" needs (see discard below): the next
        get() after a discard() starts fresh, since the row is gone."""
        env = self._envs.get(session_id)
        if env is None:
            env = Env()
            self._envs[session_id] = env
        return env

    def discard(self, session_id: int) -> None:
        """The three points a test/preview session's own lifecycle ends —
        ChatService.delete_session, ChatService.close_session, a test-
        session reset, and create_preview_session's own bulk cleanup of
        the previous preview session — call this so a gone session's own
        Env doesn't linger here forever. A no-op for a live/imported
        session id (never registered here in the first place)."""
        self._envs.pop(session_id, None)
