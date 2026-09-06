"""Which Automaton a caller gets back depends on which method it calls:
published-only, a session's own pinned revision, or the in-progress
draft — never an implicit "whatever's on disk right now".
"""
from __future__ import annotations

import pytest

from project.project_service import ProjectService
from session import Session

pytestmark = pytest.mark.regression

PROJECT_ID = "proj"


def _index_yml(state_key: str) -> str:
    return f"""
project:
  id: {PROJECT_ID}
init-action:
  target: {state_key}
states:
  {state_key}:
    ui-label: {state_key}
    contextual-prompt: hi
"""


TWO_STATE_YML = f"""
project:
  id: {PROJECT_ID}
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


def _save(db, content: str) -> None:
    db.save_project_files(PROJECT_ID, {"index.yml": content.encode("utf-8")}, {"index.yml": "text/yaml"})


def _session(db, revision: int, start_state: str, type: str = "live") -> int:
    return db.create_chat_session(
        username=Session().user, project_id=PROJECT_ID, revision=revision, start_state=start_state, type=type,
    )


@pytest.fixture
def project_service(db) -> ProjectService:
    db.ensure_project(PROJECT_ID)
    db.set_active_project_id(PROJECT_ID, Session().user)
    return ProjectService(db)


def test_load_at_revision_caches_by_project_and_revision(db, project_service):
    _save(db, _index_yml("a"))
    db.publish_project(PROJECT_ID)  # revision 0
    _save(db, _index_yml("b"))  # forks to revision 1
    loader = project_service._automaton_loader

    rev0 = loader.load_at_revision(PROJECT_ID, 0)
    rev1 = loader.load_at_revision(PROJECT_ID, 1)

    assert set(rev0.states) == {"", "a"}
    assert set(rev1.states) == {"", "b"}
    # Cached under distinct keys — a second call for the same revision
    # returns the exact same object, never silently re-resolving from the
    # other one.
    assert loader.load_at_revision(PROJECT_ID, 0) is rev0
    assert loader.load_at_revision(PROJECT_ID, 1) is rev1


def test_the_active_automaton_needs_a_publication_and_then_ignores_the_draft_which_a_test_session_sees_instead(db, project_service):
    _save(db, _index_yml("a"))

    with pytest.raises(ValueError, match="never been published"):
        project_service.get_active_automaton_and_state()
    # A draft-typed read works even before any publication.
    draft_automaton, draft_state = project_service.get_automaton_and_state(PROJECT_ID, type='test')
    assert set(draft_automaton.states) == {"", "a"}
    assert draft_state.key == "a"

    db.publish_project(PROJECT_ID)  # published_revision = 0
    _save(db, _index_yml("b"))  # draft moves to revision 1

    automaton, state = project_service.get_active_automaton_and_state()
    assert set(automaton.states) == {"", "a"}
    assert state.key == "a"
    assert set(project_service.get_automaton_and_state(PROJECT_ID, type='test')[0].states) == {"", "b"}


def test_a_session_always_resolves_against_its_own_pinned_revision_live_or_draft_and_an_unknown_one_raises(db, project_service):
    with pytest.raises(FileNotFoundError):
        project_service.get_automaton_and_state_for_session(999999)

    _save(db, _index_yml("a"))
    db.publish_project(PROJECT_ID)  # revision 0
    old_session_id = _session(db, db.get_project_published_revision(PROJECT_ID), "a")
    draft_session_id = _session(db, db.get_project_revision(PROJECT_ID), "a", type="test")
    draft_automaton, draft_state = project_service.get_automaton_and_state_for_session(draft_session_id)
    assert set(draft_automaton.states) == {"", "a"}
    assert draft_state.key == "a"

    # A later edit + publish moves published_revision ahead — the old
    # session must keep seeing exactly what it was created against.
    _save(db, _index_yml("b"))
    db.publish_project(PROJECT_ID)  # revision 1
    new_session_id = _session(db, db.get_project_published_revision(PROJECT_ID), "b")

    automaton, state = project_service.get_automaton_and_state_for_session(old_session_id)
    assert set(automaton.states) == {"", "a"}
    assert state.key == "a"

    # A brand new session, created after the second publish, sees the new
    # revision instead — proving the difference is genuinely per-session.
    new_automaton, new_state = project_service.get_automaton_and_state_for_session(new_session_id)
    assert set(new_automaton.states) == {"", "b"}
    assert new_state.key == "b"


@pytest.mark.parametrize(("transition_in", "resolved_for"), [("live", "test"), ("test", "live")])
def test_a_transition_in_one_session_type_never_leaks_into_the_other(db, project_service, transition_in, resolved_for):
    _save(db, TWO_STATE_YML)
    db.publish_project(PROJECT_ID)
    revisions = {"live": db.get_project_published_revision(PROJECT_ID), "test": db.get_project_revision(PROJECT_ID)}

    moved = _session(db, revisions[transition_in], "a", type=transition_in)
    db.save_transition("a", "go", "b", moved, transition_log_level="INFO")
    untouched = _session(db, revisions[resolved_for], "a", type=resolved_for)

    _, state = project_service.get_automaton_and_state_for_session(untouched)

    assert state.key == "a"
