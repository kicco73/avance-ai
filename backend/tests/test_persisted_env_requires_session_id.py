"""PersistedEnv._write_memory/_write_action_set persist through
`session_id` (Tracking.session is a real FK) — passing None used to defer
that failure to the first actual write; PersistedEnv now refuses it right
at construction instead, so a caller with no real session gets a clear
error immediately rather than a working-until-you-write-to-it object.
"""
from __future__ import annotations

import pytest

from tracking.env import PersistedEnv
from tracking.fixed_project_context import FixedProjectContext

pytestmark = pytest.mark.contract

PROJECT_ID = "proj"


def test_persisted_env_raises_on_a_none_session_id(db):
    with pytest.raises(ValueError, match="session_id"):
        PersistedEnv(db, FixedProjectContext(project_id=PROJECT_ID), None)


def test_persisted_env_accepts_a_real_session_id(db):
    PersistedEnv(db, FixedProjectContext(project_id=PROJECT_ID), 1)  # must not raise
