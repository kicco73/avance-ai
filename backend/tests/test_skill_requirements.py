"""What a project cannot be built without, and what it refuses to use.

A project declares a level per service (`project.services`, see
automaton/project_services.py); on top of that, each skill works out for
itself whether the project uses it anyway (`required_by`) — never a table
the core keeps. These tests therefore assert the answers, not the
mapping — a skill added tomorrow brings its own rule and needs no line
here.
"""
from __future__ import annotations

import pytest

from conftest import installed_skill
from project.archive.automaton_loader import AutomatonLoader
from project.archive.layout import ArchiveLayout
from project.project_service import ProjectService
from system import skills
from turn.sessions.session_manager import SessionManager

pytestmark = pytest.mark.contract

INDEX = """
project:
  id: {id}{services}
init-action:
  target: start
general-prompt: hello
states:
  start:
    contextual-prompt: go
    actions:
      - name: go
        target: start
        task: |
          {task}
"""


def _automaton_and_sources(db, project_id: str, task: str, services: dict[str, str] | None):
    # None: the project never mentions a service at all, which is how
    # every project that has not thought about one is written.
    declared = "" if not services else "\n  services:" + "".join(
        f"\n    {key}: {level}" for key, level in services.items()
    )
    project_service = ProjectService(db, AutomatonLoader(db), SessionManager(db))
    db.ensure_project(project_id)
    db.save_project_files(
        project_id,
        {"index.yml": INDEX.format(id=project_id, task=task, services=declared).encode()},
        {"index.yml": "text/yaml"},
    )
    db.publish_project(project_id)
    revision = db.get_project_revision(project_id)
    automaton = project_service.get_automaton(project_id, revision)
    return automaton, ArchiveLayout.decode_text(db.get_archives(project_id, revision=revision))


def _required(db, project_id: str, task: str, services: dict[str, str] | None = None) -> set[str]:
    return set(skills.required_for(*_automaton_and_sources(db, project_id, task, services)))


def _disabled(db, project_id: str, task: str, services: dict[str, str] | None = None) -> set[str]:
    return set(skills.disabled_for(*_automaton_and_sources(db, project_id, task, services)))


def _contradicted(db, project_id: str, task: str, services: dict[str, str] | None = None) -> set[str]:
    return set(skills.contradicted_for(*_automaton_and_sources(db, project_id, task, services)))


def test_a_project_that_sends_mail_cannot_be_built_without_mail(db):
    installed_skill("mail")
    assert "mail" in _required(db, "a", "task.send_mail(user.email, 'hi')")


def test_a_project_that_messages_whatsapp_cannot_be_built_without_whatsapp(db):
    installed_skill("whatsapp")
    assert "whatsapp" in _required(db, "b", "task.whatsapp('34600000001', 'hi')")


def test_a_declared_required_service_is_required_even_when_nothing_calls_it(db):
    installed_skill("talk")
    assert "talk" in _required(db, "c", "task.prompt('x')", {"talk": "required"})


def test_a_second_skill_declared_required_answers_for_itself(db):
    installed_skill("listen")
    assert "listen" in _required(db, "c2", "task.prompt('x')", {"listen": "required"})


def test_optional_and_disabled_declarations_never_make_a_service_required(db):
    assert "talk" not in _required(db, "d", "task.prompt('x')", {"talk": "disabled"})
    assert "talk" not in _required(db, "d2", "task.prompt('x')", {"talk": "optional"})
    assert "talk" not in _required(db, "g", "task.prompt('x')")


def test_a_project_that_asks_for_nothing_requires_nothing(db):
    assert _required(db, "e", "task.prompt('x')") == set()


def test_what_a_project_never_calls_is_never_required(db):
    installed_skill("mail")
    installed_skill("talk")
    required = _required(db, "f", "task.send_mail(user.email, 'hi')", {"talk": "disabled"})
    assert required == {"mail"}, "only what the automaton actually uses"


def test_a_disabled_service_is_reported_as_disabled(db):
    installed_skill("talk")
    assert _disabled(db, "h", "task.prompt('x')", {"talk": "disabled"}) == {"talk"}
    assert _disabled(db, "i", "task.prompt('x')", {"talk": "required"}) == set()


def test_calling_a_service_the_project_disabled_is_a_contradiction(db):
    installed_skill("mail")
    assert _contradicted(db, "j", "task.send_mail(user.email, 'hi')", {"mail": "disabled"}) == {"mail"}
    assert _contradicted(db, "k", "task.prompt('x')", {"mail": "disabled"}) == set()


def test_the_deprecated_talk_enabled_flag_still_says_required_or_disabled(db):
    project_service = ProjectService(db, AutomatonLoader(db), SessionManager(db))
    for project_id, flag, expected in (("l", "true", "required"), ("m", "false", "disabled")):
        db.ensure_project(project_id)
        db.save_project_files(
            project_id,
            {"index.yml": INDEX.format(
                id=project_id, task="task.prompt('x')", services=f"\n  talk-enabled: {flag}",
            ).encode()},
            {"index.yml": "text/yaml"},
        )
        db.publish_project(project_id)
        automaton = project_service.get_automaton(project_id, db.get_project_revision(project_id))
        assert automaton.services.as_raw() == {"talk": expected}
        assert any("talk-enabled is deprecated" in warning for warning in automaton.build_warnings)
