"""tracking.automaton_namespace.AutomatonNamespace — automaton.<project>.
state/env.<key> runtime resolution. Every failure mode resolves to None
and records a SystemWarning instead of raising.
"""
from __future__ import annotations

import pytest

from project.project_service import ProjectService
from tracking.automaton_namespace import AutomatonNamespace

pytestmark = pytest.mark.contract

USERNAME = "user"

# The family both the caller (scoped_to) and "observed" itself declare —
# automaton.* visibility requires an exact match on both sides (see
# tracking.automaton_namespace's own docstring).
FAMILY = "shared_family"


def _publish(db, project_id: str, index_yml: str) -> None:
    """A lighter-weight publish that skips the save pipeline."""
    db.ensure_project(project_id)
    db.save_project_files(project_id, {"index.yml": index_yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(project_id)
    db.set_project_metadata(project_id, ui_label=None, ui_description=None)


BASIC_YML = f"""
project:
  id: observed
  family: {FAMILY}
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""

WITH_ENV_YML = f"""
project:
  id: observed
  family: {FAMILY}
env:
  visits:
    ui-description: Visit counter
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""


def _namespace(db, family=FAMILY):
    return AutomatonNamespace(db, ProjectService(db)).scoped_to(family)


def _session(db, project_id="observed") -> int:
    return db.create_chat_session(
        username=USERNAME, project_id=project_id, revision=db.get_project_published_revision(project_id)
    )


def _warning_kinds(db, project_id="observed") -> list[str]:
    return [w["kind"] for w in db.get_system_warnings(USERNAME, project_id)]


def test_a_project_that_is_unknown_unfamilied_or_of_another_family_is_indistinguishable_and_warns_project_not_found(db):
    """A caller with no family at all can't observe anything, and another
    family is never distinguishable from "doesn't exist" (see
    _ProjectProxy._resolve). A session must exist first for the family
    check itself to be the step that fails."""
    assert _namespace(db).nonexistent_project.state is None
    assert _warning_kinds(db, "nonexistent_project") == ["project_not_found"]

    _publish(db, "observed", BASIC_YML)
    _session(db)

    assert _namespace(db, family=None).observed.state is None
    assert _namespace(db, family="some_other_family").observed.state is None
    assert _warning_kinds(db) == ["project_not_found", "project_not_found"]


def test_state_needs_a_session_of_its_own_warning_no_session_otherwise(db):
    _publish(db, "observed", BASIC_YML)

    assert _namespace(db).observed.state is None
    assert _warning_kinds(db) == ["no_session"]

    _session(db)
    assert _namespace(db).observed.state == "a"


def test_an_env_key_resolves_to_its_action_set_value_or_to_none_warning_only_when_it_was_never_declared(db):
    _publish(db, "observed", WITH_ENV_YML)
    session_id = _session(db)

    assert _namespace(db).observed.env.never_declared is None
    assert _warning_kinds(db) == ["env_key_not_declared"]

    # Declared but never set: None, and no warning of its own.
    assert _namespace(db).observed.env.visits is None

    db.set_action_env(session_id, {"visits": 3})
    assert _namespace(db).observed.env.visits == 3
    assert _warning_kinds(db) == ["env_key_not_declared"]  # still just the one from above
