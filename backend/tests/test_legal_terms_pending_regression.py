"""Regression for the production bug: accepting a project's legal terms
must stick even when the project has unpublished draft edits (which fork
every Archive row, including legal/terms.md, into a new id identical in
content but distinct from the published one). accept_legal_terms and
legal_terms_pending must resolve the *same* (published) revision, or
acceptance can never satisfy the pending check.
"""
from __future__ import annotations

import pytest

from project.project_service import ProjectService

pytestmark = pytest.mark.regression

PROJECT_NAME = "proj"
USERNAME = "user"

INDEX_YML = """
init-action:
  target: a
states:
  a:
    ui-label: a
    contextual-prompt: hi
"""

TERMS_MD = b"# Terms\n\nAccept to continue.\n"


@pytest.fixture
def project_service(db) -> ProjectService:
    db.ensure_project(PROJECT_NAME)
    db.save_project_files(
        PROJECT_NAME,
        {"index.yml": INDEX_YML.encode("utf-8"), "legal/terms.md": TERMS_MD},
        {"index.yml": "text/yaml", "legal/terms.md": "text/markdown"},
    )
    db.publish_project(PROJECT_NAME)  # published_revision = 0
    return ProjectService(db)


def test_accept_legal_terms_resolves_pending_even_with_a_diverged_draft(db, project_service):
    # An unrelated draft edit forks every Archive row (including
    # legal/terms.md) to revision 1, identical content but a new row id —
    # exactly what happened to "TTM prototype" in production
    # (revision=1, published_revision=0, 5 draft edits).
    db.save_project_files(
        PROJECT_NAME, {"index.yml": INDEX_YML.encode("utf-8")}, {"index.yml": "text/yaml"}
    )
    assert db.get_project_revision(PROJECT_NAME) != db.get_project_published_revision(PROJECT_NAME)

    assert project_service.legal_terms_pending(USERNAME, PROJECT_NAME) is True

    project_service.accept_legal_terms(USERNAME, PROJECT_NAME)

    assert project_service.legal_terms_pending(USERNAME, PROJECT_NAME) is False


def test_accept_legal_terms_still_asks_again_if_the_published_terms_later_change(db, project_service):
    project_service.accept_legal_terms(USERNAME, PROJECT_NAME)
    assert project_service.legal_terms_pending(USERNAME, PROJECT_NAME) is False

    db.save_project_files(
        PROJECT_NAME, {"legal/terms.md": b"# Terms\n\nNew content.\n"}, {"legal/terms.md": "text/markdown"}
    )
    db.publish_project(PROJECT_NAME)  # published_revision advances to the new terms

    assert project_service.legal_terms_pending(USERNAME, PROJECT_NAME) is True
