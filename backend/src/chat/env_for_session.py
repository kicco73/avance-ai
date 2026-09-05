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
