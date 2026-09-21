from __future__ import annotations

from tracking.env import Env, PersistedEnv
from tracking.fixed_project_context import FixedProjectContext

from turn.ephemeral_env_registry import EphemeralEnvRegistry
from turn.turn_transaction import TurnDbInterface

EPHEMERAL_SESSION_TYPES = ("test", "preview")


def env_for_session(db: TurnDbInterface, session: dict) -> Env:
    if session["type"] in EPHEMERAL_SESSION_TYPES:
        return EphemeralEnvRegistry().get(session["id"])
    return PersistedEnv(
        db, FixedProjectContext(project_id=session["project_id"]), session["id"], username=session["username"],
    )
