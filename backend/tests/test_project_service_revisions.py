"""Which Automaton a caller gets back depends on which method it calls:
published-only, a session's own pinned revision, or the in-progress
draft — never an implicit "whatever's on disk right now".
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


def _save_index_yml(db, state_key: str) -> None:
    db.save_project_files(
        PROJECT_NAME, {"index.yml": _index_yml(state_key).encode("utf-8")}, {"index.yml": "text/yaml"}
    )


TWO_STATE_YML = """
init-action:
  target: a
states:
  a:
    ui-label: a
    contextual-prompt: hi
    actions:
      - name: go
        target: b
  b:
    ui-label: b
    contextual-prompt: there
"""


def _save_two_state_index_yml(db) -> None:
    db.save_project_files(
        PROJECT_NAME, {"index.yml": TWO_STATE_YML.encode("utf-8")}, {"index.yml": "text/yaml"}
    )


@pytest.fixture
def project_service(db) -> ProjectService:
    db.ensure_project(PROJECT_NAME)
    db.set_active_project_name(PROJECT_NAME, Session().user)
    return ProjectService(db)


def test_load_project_at_revision_caches_by_project_and_revision(db, project_service):
    _save_index_yml(db, "a")
    db.publish_project(PROJECT_NAME)  # revision 0
    _save_index_yml(db, "b")  # forks to revision 1

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
    _save_index_yml(db, "a")

    with pytest.raises(ValueError, match="never been published"):
        project_service.get_active_automaton_and_state()


def test_get_active_automaton_and_state_uses_the_published_revision_not_the_draft(db, project_service):
    _save_index_yml(db, "a")
    db.publish_project(PROJECT_NAME)  # published_revision = 0
    _save_index_yml(db, "b")  # draft moves to revision 1

    automaton, state = project_service.get_active_automaton_and_state()

    assert set(automaton.states) == {"", "a"}
    assert state.key == "a"


def test_get_draft_automaton_and_state_works_for_a_never_published_project(db, project_service):
    _save_index_yml(db, "a")

    automaton, state = project_service.get_draft_automaton_and_state(PROJECT_NAME)

    assert set(automaton.states) == {"", "a"}
    assert state.key == "a"


def test_get_draft_automaton_and_state_sees_the_in_progress_draft(db, project_service):
    _save_index_yml(db, "a")
    db.publish_project(PROJECT_NAME)
    _save_index_yml(db, "b")

    automaton, _ = project_service.get_draft_automaton_and_state(PROJECT_NAME)

    assert set(automaton.states) == {"", "b"}


def test_get_automaton_and_state_for_session_uses_that_sessions_own_pinned_revision(db, project_service):
    _save_index_yml(db, "a")
    db.publish_project(PROJECT_NAME)  # revision 0
    old_session_id = db.create_chat_session(
        username=Session().user, project_name=PROJECT_NAME, start_state="a",
    )

    # A later edit + publish moves published_revision ahead — the old
    # session must keep seeing exactly what it was created against, not
    # whatever's newly published.
    _save_index_yml(db, "b")
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
    _save_index_yml(db, "a")
    draft_session_id = db.create_draft_chat_session(
        username=Session().user, project_name=PROJECT_NAME, start_state="a",
    )

    automaton, state = project_service.get_automaton_and_state_for_session(draft_session_id)

    assert set(automaton.states) == {"", "a"}
    assert state.key == "a"


def test_get_automaton_and_state_for_session_raises_for_an_unknown_session(db, project_service):
    with pytest.raises(FileNotFoundError):
        project_service.get_automaton_and_state_for_session(999999)


def test_get_automaton_and_state_for_session_does_not_leak_a_native_transition_into_a_test_session(db, project_service):
    _save_two_state_index_yml(db)
    db.publish_project(PROJECT_NAME)
    native_session_id = db.create_chat_session(
        username=Session().user, project_name=PROJECT_NAME, start_state="a",
    )
    db.save_transition("a", "go", "b", native_session_id, transition_log_level="INFO")

    test_session_id = db.create_draft_chat_session(
        username=Session().user, project_name=PROJECT_NAME, start_state="a",
    )

    _, state = project_service.get_automaton_and_state_for_session(test_session_id)

    assert state.key == "a"


def test_get_automaton_and_state_for_session_does_not_leak_a_test_transition_into_a_native_session(db, project_service):
    _save_two_state_index_yml(db)
    db.publish_project(PROJECT_NAME)
    test_session_id = db.create_draft_chat_session(
        username=Session().user, project_name=PROJECT_NAME, start_state="a",
    )
    db.save_transition("a", "go", "b", test_session_id, transition_log_level="INFO")

    native_session_id = db.create_chat_session(
        username=Session().user, project_name=PROJECT_NAME, start_state="a",
    )

    _, state = project_service.get_automaton_and_state_for_session(native_session_id)

    assert state.key == "a"
