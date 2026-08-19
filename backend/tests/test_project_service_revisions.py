"""Regression coverage for the revision-security hardening: which
Automaton a caller actually gets back must depend on *which method* it
calls (published-only, a session's own pinned revision, or the
in-progress draft — see ProjectService's own get_active_automaton_and_
state/get_automaton_and_state_for_session/get_active_draft_automaton_and_
state), never on an implicit "whatever's on disk right now" the way
_load_project alone used to give every caller before this.
"""
from __future__ import annotations

import pytest

from project.project_service import ProjectService
from session import Session

pytestmark = pytest.mark.regression

PROJECT_NAME = "proj"


def _index_yml(state_key: str) -> str:
    return f"""
init-action:
  target: {state_key}
states:
  {state_key}:
    ui-label: {state_key}
    contextual-prompt: hi
"""


@pytest.fixture
def project_service(db) -> ProjectService:
    db.ensure_project(PROJECT_NAME)
    db.set_active_project_name(PROJECT_NAME, Session().user)
    return ProjectService(db)


def test_load_project_at_revision_caches_by_project_and_revision(db, project_service):
    db.save_project_files(PROJECT_NAME, {"index.yml": _index_yml("a")})
    db.publish_project(PROJECT_NAME)  # revision 0
    db.save_project_files(PROJECT_NAME, {"index.yml": _index_yml("b")})  # forks to revision 1

    rev0 = project_service._load_project_at_revision(PROJECT_NAME, 0)
    rev1 = project_service._load_project_at_revision(PROJECT_NAME, 1)

    assert set(rev0.states) == {"", "a"}
    assert set(rev1.states) == {"", "b"}
    # Cached under distinct keys — a second call for the same revision
    # returns the exact same object, never silently re-resolving from the
    # other one.
    assert project_service._load_project_at_revision(PROJECT_NAME, 0) is rev0
    assert project_service._load_project_at_revision(PROJECT_NAME, 1) is rev1


def test_get_active_automaton_and_state_raises_when_never_published(db, project_service):
    db.save_project_files(PROJECT_NAME, {"index.yml": _index_yml("a")})

    with pytest.raises(ValueError, match="never been published"):
        project_service.get_active_automaton_and_state()


def test_get_active_automaton_and_state_uses_the_published_revision_not_the_draft(db, project_service):
    db.save_project_files(PROJECT_NAME, {"index.yml": _index_yml("a")})
    db.publish_project(PROJECT_NAME)  # published_revision = 0
    db.save_project_files(PROJECT_NAME, {"index.yml": _index_yml("b")})  # draft moves to revision 1

    automaton, state = project_service.get_active_automaton_and_state()

    assert set(automaton.states) == {"", "a"}
    assert state.key == "a"


def test_get_active_draft_automaton_and_state_works_for_a_never_published_project(db, project_service):
    db.save_project_files(PROJECT_NAME, {"index.yml": _index_yml("a")})

    automaton, state = project_service.get_active_draft_automaton_and_state()

    assert set(automaton.states) == {"", "a"}
    assert state.key == "a"


def test_get_active_draft_automaton_and_state_sees_the_in_progress_draft(db, project_service):
    db.save_project_files(PROJECT_NAME, {"index.yml": _index_yml("a")})
    db.publish_project(PROJECT_NAME)
    db.save_project_files(PROJECT_NAME, {"index.yml": _index_yml("b")})

    automaton, _ = project_service.get_active_draft_automaton_and_state()

    assert set(automaton.states) == {"", "b"}


def test_get_automaton_and_state_for_session_uses_that_sessions_own_pinned_revision(db, project_service):
    db.save_project_files(PROJECT_NAME, {"index.yml": _index_yml("a")})
    db.publish_project(PROJECT_NAME)  # revision 0
    old_session_id = db.create_chat_session(
        username=Session().user, project_name=PROJECT_NAME, start_state="a",
    )

    # A later edit + publish moves published_revision ahead — the old
    # session must keep seeing exactly what it was created against, not
    # whatever's newly published.
    db.save_project_files(PROJECT_NAME, {"index.yml": _index_yml("b")})
    db.publish_project(PROJECT_NAME)  # revision 1

    automaton, state = project_service.get_automaton_and_state_for_session(old_session_id)

    assert set(automaton.states) == {"", "a"}
    assert state.key == "a"

    # A brand new session, created after the second publish, sees the new
    # revision instead — proving the difference is genuinely per-session,
    # not some other global effect.
    new_session_id = db.create_chat_session(
        username=Session().user, project_name=PROJECT_NAME, start_state="b",
    )
    new_automaton, new_state = project_service.get_automaton_and_state_for_session(new_session_id)
    assert set(new_automaton.states) == {"", "b"}
    assert new_state.key == "b"


def test_get_automaton_and_state_for_session_works_for_a_draft_session_too(db, project_service):
    db.save_project_files(PROJECT_NAME, {"index.yml": _index_yml("a")})
    draft_session_id = db.create_draft_chat_session(
        username=Session().user, project_name=PROJECT_NAME, start_state="a",
    )

    automaton, state = project_service.get_automaton_and_state_for_session(draft_session_id)

    assert set(automaton.states) == {"", "a"}
    assert state.key == "a"


def test_get_automaton_and_state_for_session_raises_for_an_unknown_session(db, project_service):
    with pytest.raises(FileNotFoundError):
        project_service.get_automaton_and_state_for_session(999999)
