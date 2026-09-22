"""event.<project>.state/env.<key> runtime resolution. Every failure mode
resolves to None and records a SystemWarning instead of raising.
"""
from __future__ import annotations

from automaton.choice import ChoiceSelection

import pytest

from automaton.automaton_builder import AutomatonBuilder
from system import bus
from system.bus import POINT_CORE_SERVICES
from turn.sessions.session_manager import SessionManager
from project.archive.automaton_loader import AutomatonLoader
from project.project_service import ProjectService

pytestmark = pytest.mark.contract

USERNAME = "user"
FAMILY = "shared_family"


def _publish(db, project_id: str, index_yml: str) -> None:
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
    input-processor: ai
    contextual-prompt: hi
"""

WITH_ENV_YML = f"""
project:
  id: observed
  family: {FAMILY}
env:
  visits:
    type: number
    ui-description: Visit counter
init-action:
  target: a
states:
  a:
    ui-label: A
    input-processor: ai
    contextual-prompt: hi
"""


def _caller(family: str | None):
    family_line = f"  family: {family}\n" if family is not None else ""
    return AutomatonBuilder().build({"index.yml": f"project:\n  id: caller\n{family_line}" + BASIC_YML.split("family:")[1].split("\n", 1)[1]})


def _namespace(db, event_namespace, family=FAMILY):
    project_service = ProjectService(db, AutomatonLoader(db), SessionManager(db))
    bus.contribute(POINT_CORE_SERVICES, lambda registry: registry.update({"db": db, "project_service": project_service}))
    return event_namespace.scope_for(_caller(family), ChoiceSelection.NONE)


def _session(db, project_id="observed") -> int:
    return db.create_chat_session(
        username=USERNAME, project_id=project_id, revision=db.get_project_published_revision(project_id)
    )


def _warning_kinds(db, project_id="observed") -> list[str]:
    return [w["kind"] for w in db.get_system_warnings(USERNAME, project_id)]


def test_a_project_that_is_unknown_unfamilied_or_of_another_family_is_indistinguishable_and_warns_project_not_found(db, event_namespace):
    assert _namespace(db, event_namespace).nonexistent_project.state is None
    assert _warning_kinds(db, "nonexistent_project") == ["project_not_found"]

    _publish(db, "observed", BASIC_YML)
    _session(db)

    assert _namespace(db, event_namespace, family=None).observed.state is None
    assert _namespace(db, event_namespace, family="some_other_family").observed.state is None
    assert _warning_kinds(db) == ["project_not_found", "project_not_found"]


def test_state_needs_a_session_of_its_own_warning_no_session_otherwise(db, event_namespace):
    _publish(db, "observed", BASIC_YML)

    assert _namespace(db, event_namespace).observed.state is None
    assert _warning_kinds(db) == ["no_session"]

    _session(db)
    assert _namespace(db, event_namespace).observed.state == "a"


def test_an_env_key_resolves_to_its_action_set_value_or_to_none_warning_only_when_it_was_never_declared(db, event_namespace):
    _publish(db, "observed", WITH_ENV_YML)
    session_id = _session(db)

    assert _namespace(db, event_namespace).observed.env.never_declared is None
    assert _warning_kinds(db) == ["env_key_not_declared"]

    assert _namespace(db, event_namespace).observed.env.visits is None

    db.set_action_env(session_id, {"visits": 3})
    assert _namespace(db, event_namespace).observed.env.visits == 3
    assert _warning_kinds(db) == ["env_key_not_declared"]
