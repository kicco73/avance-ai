"""A revision already stored in the Archive table must stay loadable
forever — ChatSession.project_revision pins every native/imported
session to the revision published when it was created, and every
endpoint touching such a session (state, messages, timeline, tests…)
builds that exact revision. When AutomatonBuilder started requiring
`project.id`, revisions stored before that (no `project:` section at
all) stopped building, and every session pinned to one answered 500
"project.id None is required…" — on whichever endpoint happened to
touch it. The loader now supplies the Archive row's own project_id as
the legacy default; an *upload* without project.id is still rejected.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton_builder import AutomatonBuilder
from automaton.build_error import AutomatonBuildError
from chat.session_manager import ChatSessionManager
from project.archive.automaton_loader import AutomatonLoader

pytestmark = pytest.mark.regression

PROJECT_ID = "legacy_proj"

# What an index.yml looked like before `project:` existed at all.
PRE_PROJECT_SECTION_YML = """
avance-version: "1.7.0"
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""

# A `project:` section that predates the `id` field.
PRE_PROJECT_ID_YML = """
project:
  ui-label: Old label
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""

CURRENT_YML = """
project:
  id: legacy_proj
  revision: 1
init-action:
  target: b
states:
  b:
    ui-label: B
    contextual-prompt: hi
"""


def _store(db, yml: str) -> int:
    db.ensure_project(PROJECT_ID)
    db.save_project_files(PROJECT_ID, {"index.yml": yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(PROJECT_ID)
    return db.get_project_published_revision(PROJECT_ID)


@pytest.mark.parametrize("legacy_yml", [PRE_PROJECT_SECTION_YML, PRE_PROJECT_ID_YML])
def test_a_stored_revision_without_project_id_still_loads_under_its_rows_own_id(db, legacy_yml):
    old_revision = _store(db, legacy_yml)
    new_revision = _store(db, CURRENT_YML)
    assert new_revision != old_revision

    loader = AutomatonLoader(db)
    old = loader.load_at_revision(PROJECT_ID, old_revision)
    new = loader.load_at_revision(PROJECT_ID, new_revision)

    assert old.project_id == PROJECT_ID
    assert old.init_action.target == "a"
    assert new.init_action.target == "b"


def test_the_legacy_default_never_applies_outside_the_loader():
    """The same YAML through the builder directly — an upload, an editor
    save — is rejected exactly as before: only a row already in the
    Archive table earns the default."""
    with pytest.raises(ValueError, match="project"):
        AutomatonBuilder().build({"index.yml": PRE_PROJECT_SECTION_YML})
    with pytest.raises(ValueError, match="project.id"):
        AutomatonBuilder().build({"index.yml": PRE_PROJECT_ID_YML})


BROKEN_ON_ENTER_YML = """
project:
  id: legacy_proj
init-action:
  target: a
  on-enter: celebrate()
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""


def test_a_stored_revision_that_no_longer_builds_names_itself(db):
    """Whatever else can go stale in a stored revision surfaces on some
    unrelated endpoint: the resulting AutomatonBuildError must carry
    which project/revision it is (see ProjectManager.prepare_update's own
    equivalent stamping for a live save) — the builder's own message stays
    exactly as raised; the "which stored revision" framing lives in
    `.detail` instead, not merged into the message itself."""
    broken = _store(db, BROKEN_ON_ENTER_YML)

    with pytest.raises(AutomatonBuildError) as exc_info:
        AutomatonLoader(db).load_at_revision(PROJECT_ID, broken)

    exc = exc_info.value
    assert exc.project_id == PROJECT_ID
    assert exc.revision == broken
    assert f"Project '{PROJECT_ID}', stored revision {broken}" in exc.detail
    assert "Project" not in str(exc)  # the builder's own message, untouched


def _open_session_on(db, revision: int) -> int:
    return db.create_chat_session(
        username="user", project_id=PROJECT_ID, revision=revision,
        datetime_start=datetime.utcnow(), datetime_end=datetime.utcnow(),
        start_state="a", end_state="a",
    )


def test_a_broken_revision_force_closes_its_own_open_sessions(db):
    broken = _store(db, BROKEN_ON_ENTER_YML)
    session_id = _open_session_on(db, broken)
    loader = AutomatonLoader(db, session_manager=ChatSessionManager(db))

    with pytest.raises(AutomatonBuildError):
        loader.load_at_revision(PROJECT_ID, broken)

    closed = db.get_chat_session(session_id)
    assert closed["close_reason"] == "revision-invalid"
    assert closed["closed_at"] is not None


def test_a_broken_revision_never_touches_a_different_revisions_session(db):
    broken = _store(db, BROKEN_ON_ENTER_YML)
    other_revision = _store(db, CURRENT_YML)
    other_session_id = _open_session_on(db, other_revision)
    loader = AutomatonLoader(db, session_manager=ChatSessionManager(db))

    with pytest.raises(AutomatonBuildError):
        loader.load_at_revision(PROJECT_ID, broken)

    assert db.get_chat_session(other_session_id)["close_reason"] is None


def test_with_no_session_manager_it_still_raises_but_closes_nothing(db):
    """AutomatonLoader(db) with no session_manager has no session of its
    own to worry about — the load must still fail, just without the
    close side effect."""
    broken = _store(db, BROKEN_ON_ENTER_YML)
    session_id = _open_session_on(db, broken)
    loader = AutomatonLoader(db)

    with pytest.raises(AutomatonBuildError):
        loader.load_at_revision(PROJECT_ID, broken)

    assert db.get_chat_session(session_id)["close_reason"] is None


def test_the_close_sweep_only_runs_once_per_broken_revision(db, monkeypatch):
    """load_at_revision is hit from several per-request read paths
    (ProjectInspector) — a revision under active use must not re-run the
    close sweep (a DB query) on every single failed load."""
    broken = _store(db, BROKEN_ON_ENTER_YML)
    loader = AutomatonLoader(db, session_manager=ChatSessionManager(db))
    calls = []
    original = db.list_live_sessions_for_revision

    def _counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(db, "list_live_sessions_for_revision", _counting)

    for _ in range(3):
        with pytest.raises(AutomatonBuildError):
            loader.load_at_revision(PROJECT_ID, broken)

    assert len(calls) == 1
