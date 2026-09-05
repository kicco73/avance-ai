"""tracking.legacy_env_migration."""
from __future__ import annotations

from datetime import datetime

import pytest

from db.models import Tracking
from tracking.legacy_env_migration import migrate_env_rows

pytestmark = pytest.mark.regression

USERNAME = "user"
PROJECT_ID = "migr_proj"


def _publish(db, env_keys: list[str]) -> None:
    env_section = "\n".join(f"  {key}:" for key in env_keys)
    yml = f"""
project:
  id: {PROJECT_ID}
env:
{env_section}
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, {"index.yml": yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(PROJECT_ID)


def _session(db, type: str = "live") -> int:
    return db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a", type=type,
    )


def test_stray_test_session_env_rows_are_deleted(db):
    _publish(db, ["a"])
    test_session = _session(db, type="test")
    db.set_env(test_session, {"leaked": "value"})
    db.set_action_env(test_session, {"leaked": 1})
    live_session = _session(db, type="live")
    db.set_action_env(live_session, {"a": 1})

    migrate_env_rows(db)

    assert Tracking.select().where(Tracking.session == test_session).count() == 0
    assert db.get_action_env(PROJECT_ID, USERNAME) == {"a": 1}


def test_preview_session_env_rows_are_deleted_too(db):
    _publish(db, ["a"])
    preview_session = _session(db, type="preview")
    db.set_env(preview_session, {"leaked": "value"})

    migrate_env_rows(db)

    assert Tracking.select().where(Tracking.session == preview_session).count() == 0


def test_orphaned_live_action_env_keys_are_dropped_others_kept(db):
    _publish(db, ["old_key", "keep_key"])
    live_session = _session(db, type="live")
    db.set_action_env(live_session, {"old_key": 1, "keep_key": 2})
    _publish(db, ["keep_key"])

    migrate_env_rows(db)

    assert db.get_action_env(PROJECT_ID, USERNAME) == {"keep_key": 2}


def test_a_project_never_published_is_skipped_not_treated_as_declaring_nothing(db):
    db.ensure_project(PROJECT_ID)
    live_session = db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID, revision=0,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a", type="live",
    )
    db.set_action_env(live_session, {"a": 1})

    migrate_env_rows(db)

    assert db.get_action_env(PROJECT_ID, USERNAME) == {"a": 1}


def test_no_op_when_nothing_needs_cleanup(db):
    _publish(db, ["a"])
    live_session = _session(db, type="live")
    db.set_action_env(live_session, {"a": 1})

    migrate_env_rows(db)

    assert db.get_action_env(PROJECT_ID, USERNAME) == {"a": 1}
