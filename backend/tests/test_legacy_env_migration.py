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


def test_every_stray_test_or_preview_env_row_is_deleted_while_the_live_ones_survive(db):
    _publish(db, ["a"])
    test_session = _session(db, type="test")
    preview_session = _session(db, type="preview")
    db.set_env(test_session, {"leaked": "value"})
    db.set_action_env(test_session, {"leaked": 1})
    db.set_env(preview_session, {"leaked": "value"})
    live_session = _session(db, type="live")
    db.set_action_env(live_session, {"a": 1})

    migrate_env_rows(db)

    assert Tracking.select().where(Tracking.session == test_session).count() == 0
    assert Tracking.select().where(Tracking.session == preview_session).count() == 0
    assert db.get_action_env(PROJECT_ID, USERNAME) == {"a": 1}


def test_an_orphaned_live_key_is_dropped_even_when_a_stray_test_row_landed_more_recently(db):
    """Db.get_action_env merges every session type for a (project, user)
    pair — before deleting stray test/preview rows first, a test session's
    own row landing more recently than the live session's own could get
    mistaken for "the" current action_env, masking the live session's real
    orphaned key entirely (see migrate_env_rows' own reordered steps)."""
    _publish(db, ["old_key", "keep_key"])
    live_session = _session(db, type="live")
    db.set_action_env(live_session, {"old_key": 1, "keep_key": 2})
    test_session = _session(db, type="test")
    db.set_action_env(test_session, {"unrelated": 1})
    _publish(db, ["keep_key"])  # "old_key" is no longer declared

    migrate_env_rows(db)

    assert Tracking.select().where(Tracking.session == test_session).count() == 0
    assert db.get_action_env(PROJECT_ID, USERNAME) == {"keep_key": 2}


def test_a_project_that_was_never_published_or_no_longer_builds_is_skipped_rather_than_wiped_or_crashed(db):
    """The exact boot-crash this regressed into: a published revision that
    doesn't build under today's AutomatonBuilder rules must never take the
    whole migration — and thus the whole boot — down with it.
    ProjectManager.recompute_availability (run right after every
    migration, at boot) is what actually pauses it."""
    db.ensure_project(PROJECT_ID)
    unpublished_session = db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID, revision=0,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a", type="live",
    )
    db.set_action_env(unpublished_session, {"a": 1})

    migrate_env_rows(db)  # must not raise
    assert db.get_action_env(PROJECT_ID, USERNAME) == {"a": 1}

    _publish(db, ["a"])
    db.set_action_env(_session(db, type="live"), {"a": 1})
    from db.models import Archive
    revision = db.get_project_published_revision(PROJECT_ID)
    Archive.update(content=b"not: [valid, yaml: at all").where(
        (Archive.project == PROJECT_ID) & (Archive.archive_name == "index.yml") & (Archive.revision == revision)
    ).execute()

    migrate_env_rows(db)  # must not raise
    assert db.get_action_env(PROJECT_ID, USERNAME) == {"a": 1}


def test_a_clean_project_is_left_exactly_as_it_was(db):
    _publish(db, ["a"])
    db.set_action_env(_session(db, type="live"), {"a": 1})

    migrate_env_rows(db)

    assert db.get_action_env(PROJECT_ID, USERNAME) == {"a": 1}
