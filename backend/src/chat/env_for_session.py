"""The single place that decides which Env a session's own chat turns,
on-enter scripts, and Inspector reads run against.

Production bug this replaces: every one of these call sites used to build
a PersistedEnv unconditionally, regardless of the session's own type.
PersistedEnv's write path resolved "the latest live session" for
(project, user) rather than the session that actually produced the
value (see tracking.env.PersistedEnv and db.tracking.Db.set_env) — so a
`test`/`preview` session's own turn silently attached its env writes to
the request user's real *live* session, and the resulting Tracking row
outlived every reset. A `test`/`preview` session's env is in-memory only
(EphemeralEnvRegistry) from here on: it never touches the database, so it
can never leak into (or survive as) another session's row. A `live`/
`imported` session keeps using PersistedEnv, now pinned to its own
session id so a write always lands on the session that made it.
"""
from __future__ import annotations

from db import Db
from tracking.env import Env, PersistedEnv
from tracking.fixed_project_context import FixedProjectContext

from .ephemeral_env_registry import EphemeralEnvRegistry

EPHEMERAL_SESSION_TYPES = ("test", "preview")


def env_for_session(db: Db, session: dict) -> Env:
    if session["type"] in EPHEMERAL_SESSION_TYPES:
        return EphemeralEnvRegistry().get(session["id"])
    return PersistedEnv(
        db, FixedProjectContext(project_id=session["project_id"]), session["id"], username=session["username"],
    )
