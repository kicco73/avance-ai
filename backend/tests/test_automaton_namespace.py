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


def _namespace(db):
    return AutomatonNamespace(db, ProjectService(db)).scoped_to(FAMILY)


def test_state_resolves_to_none_and_warns_when_the_project_does_not_exist(db):
    namespace = _namespace(db)

    assert namespace.nonexistent_project.state is None

    warnings = db.get_system_warnings(USERNAME, "nonexistent_project")
    assert len(warnings) == 1
    assert warnings[0]["kind"] == "project_not_found"


def test_state_resolves_to_none_and_warns_when_the_caller_has_no_family(db):
    """A caller with no family at all can't observe anything, even a
    project that does exist and declares a family of its own — reported
    identically to a truly nonexistent project (see _ProjectProxy._resolve)."""
    _publish(db, "observed", BASIC_YML)
    namespace = AutomatonNamespace(db, ProjectService(db)).scoped_to(None)

    assert namespace.observed.state is None

    warnings = db.get_system_warnings(USERNAME, "observed")
    assert len(warnings) == 1
    assert warnings[0]["kind"] == "project_not_found"


def test_state_resolves_to_none_and_warns_when_the_family_does_not_match(db):
    """A caller declaring a *different* family than the target's own is
    reported the same "project_not_found" way as an unknown project —
    another family is never distinguishable from "doesn't exist". A
    session must exist first, or resolution would fail one step earlier
    with "no_session" instead — this test is only about the family check."""
    _publish(db, "observed", BASIC_YML)
    db.create_chat_session(username=USERNAME, project_id="observed", revision=db.get_project_published_revision("observed"))
    namespace = AutomatonNamespace(db, ProjectService(db)).scoped_to("some_other_family")

    assert namespace.observed.state is None

    warnings = db.get_system_warnings(USERNAME, "observed")
    assert len(warnings) == 1
    assert warnings[0]["kind"] == "project_not_found"


def test_state_resolves_to_none_and_warns_when_the_user_has_no_session(db):
    _publish(db, "observed", BASIC_YML)
    namespace = _namespace(db)

    assert namespace.observed.state is None

    warnings = db.get_system_warnings(USERNAME, "observed")
    assert len(warnings) == 1
    assert warnings[0]["kind"] == "no_session"


def test_state_resolves_to_the_current_state_when_a_session_exists(db):
    _publish(db, "observed", BASIC_YML)
    db.create_chat_session(username=USERNAME, project_id="observed", revision=db.get_project_published_revision("observed"))
    namespace = _namespace(db)

    assert namespace.observed.state == "a"


def test_env_key_resolves_to_none_and_warns_when_not_declared(db):
    _publish(db, "observed", WITH_ENV_YML)
    db.create_chat_session(username=USERNAME, project_id="observed", revision=db.get_project_published_revision("observed"))
    namespace = _namespace(db)

    assert namespace.observed.env.never_declared is None

    warnings = db.get_system_warnings(USERNAME, "observed")
    assert len(warnings) == 1
    assert warnings[0]["kind"] == "env_key_not_declared"


def test_env_key_resolves_to_its_action_set_value_when_declared(db):
    _publish(db, "observed", WITH_ENV_YML)
    session_id = db.create_chat_session(username=USERNAME, project_id="observed", revision=db.get_project_published_revision("observed"))
    db.set_action_env(session_id, {"visits": 3})
    namespace = _namespace(db)

    assert namespace.observed.env.visits == 3


def test_env_key_resolves_to_none_with_no_warning_when_declared_but_never_set(db):
    _publish(db, "observed", WITH_ENV_YML)
    db.create_chat_session(username=USERNAME, project_id="observed", revision=db.get_project_published_revision("observed"))
    namespace = _namespace(db)

    assert namespace.observed.env.visits is None
    assert db.get_system_warnings(USERNAME, "observed") == []
