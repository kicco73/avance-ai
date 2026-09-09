"""What a project cannot be built without.

The rule for each skill lives in that skill (`required_by`), never in a
table the core keeps: system.skills.required_for only asks. These tests
therefore assert the answers, not the mapping — a skill added tomorrow
brings its own rule and needs no line here.
"""
from __future__ import annotations

import pytest

from project.archive.automaton_loader import AutomatonLoader
from project.archive.layout import ArchiveLayout
from project.project_service import ProjectService
from system import skills
from turn.sessions.session_manager import SessionManager

pytestmark = pytest.mark.contract

INDEX = """
project:
  id: {id}{talk}
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


def _required(db, project_id: str, task: str, talk: str | None = "true") -> set[str]:
    # None: the project never mentions talk-enabled, which is how every
    # project that has not thought about speech is written.
    talk = "" if talk is None else f"\n  talk-enabled: {talk}"
    project_service = ProjectService(db, AutomatonLoader(db), SessionManager(db))
    db.ensure_project(project_id)
    db.save_project_files(
        project_id,
        {"index.yml": INDEX.format(id=project_id, task=task, talk=talk).encode()},
        {"index.yml": "text/yaml"},
    )
    db.publish_project(project_id)
    revision = db.get_project_revision(project_id)
    automaton = project_service.get_automaton(project_id, revision)
    sources = ArchiveLayout.decode_text(db.get_archives(project_id, revision=revision))
    return set(skills.required_for(automaton, sources))


def test_a_project_that_sends_mail_cannot_be_built_without_mail(db):
    assert "mail" in _required(db, "a", "task.send_mail(user.email, 'hi')")


def test_a_project_that_messages_whatsapp_cannot_be_built_without_whatsapp(db):
    assert "whatsapp" in _required(db, "b", "task.whatsapp('34600000001', 'hi')")


def test_only_an_explicit_opt_in_makes_talk_required(db):
    """talk-enabled defaults to true, so the built automaton's flag says
    nothing about whether the project ever asked to be spoken — only what
    the project itself wrote does."""
    assert "talk" in _required(db, "c", "task.prompt('x')", talk="true")
    assert "talk" not in _required(db, "d", "task.prompt('x')", talk="false")
    assert "talk" not in _required(db, "g", "task.prompt('x')", talk=None)


def test_a_project_that_asks_for_nothing_requires_nothing(db):
    assert _required(db, "e", "task.prompt('x')", talk=None) == set()


def test_what_a_project_never_calls_is_never_required(db):
    required = _required(db, "f", "task.send_mail(user.email, 'hi')", talk="false")
    assert required == {"mail"}, "only what the automaton actually uses"
