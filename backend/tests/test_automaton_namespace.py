"""tracking.automaton_namespace.AutomatonNamespace — automaton.<project>.
state/automaton.<project>.env.<key> runtime resolution (Prompt 6). Every
failure mode resolves to None and records a SystemWarning instead of
raising (see that module's own docstring for the three kinds); the
happy path reads a different project's own live current state / declared
env action-set value, for the same user.
"""
from __future__ import annotations

import pytest

from project.project_service import ProjectService
from tracking.automaton_namespace import AutomatonNamespace

pytestmark = pytest.mark.contract

USERNAME = "user"


def _publish(db, project_name: str, index_yml: str) -> None:
    """A lighter-weight publish than ProjectService._finalize_project_
    update (this file tests AutomatonNamespace's own runtime resolution
    in isolation, not the save pipeline) — but project_id -> project_name
    resolution (Prompt 8/9) still needs *some* row to look up, so this
    registers project_id == project_name directly (every project_name
    used in this file already happens to be a valid Python identifier —
    "observed" — so no separate id needs inventing)."""
    db.ensure_project(project_name)
    db.save_project_files(project_name, {"index.yml": index_yml.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(project_name)
    db.set_project_metadata(project_name, project_id=project_name, ui_label=None, ui_description=None)


BASIC_YML = """
init-action:
  target: a
states:
  a:
    ui-label: A
    contextual-prompt: hi
"""

WITH_ENV_YML = """
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


def _namespace(db) -> AutomatonNamespace:
    return AutomatonNamespace(db, ProjectService(db), lambda: USERNAME)


def test_state_resolves_to_none_and_warns_when_the_project_does_not_exist(db):
    namespace = _namespace(db)

    assert namespace.nonexistent_project.state is None

    warnings = db.get_system_warnings(USERNAME, "nonexistent_project")
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
    db.create_chat_session(username=USERNAME, project_name="observed")
    namespace = _namespace(db)

    assert namespace.observed.state == "a"


def test_env_key_resolves_to_none_and_warns_when_not_declared(db):
    _publish(db, "observed", WITH_ENV_YML)
    db.create_chat_session(username=USERNAME, project_name="observed")
    namespace = _namespace(db)

    assert namespace.observed.env.never_declared is None

    warnings = db.get_system_warnings(USERNAME, "observed")
    assert len(warnings) == 1
    assert warnings[0]["kind"] == "env_key_not_declared"


def test_env_key_resolves_to_its_action_set_value_when_declared(db):
    _publish(db, "observed", WITH_ENV_YML)
    db.create_chat_session(username=USERNAME, project_name="observed")
    db.set_action_env("observed", {"visits": 3}, USERNAME)
    namespace = _namespace(db)

    assert namespace.observed.env.visits == 3


def test_env_key_resolves_to_none_with_no_warning_when_declared_but_never_set(db):
    _publish(db, "observed", WITH_ENV_YML)
    db.create_chat_session(username=USERNAME, project_name="observed")
    namespace = _namespace(db)

    assert namespace.observed.env.visits is None
    assert db.get_system_warnings(USERNAME, "observed") == []
