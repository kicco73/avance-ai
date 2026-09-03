"""Regression for the production bug: accepting a project's legal terms
must stick even when the project has unpublished draft edits (which fork
every Archive row, including legal/terms.md, into a new id identical in
content but distinct from the published one). accept_legal_terms and
legal_terms_pending must resolve the *same* (published) revision, or
acceptance can never satisfy the pending check.
"""
from __future__ import annotations

import pytest

from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from conftest import FakeAiService, make_test_actuator_factory, make_test_job_service
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from tracking.tracking_service import TrackingService

pytestmark = pytest.mark.regression

PROJECT_ID = "proj"
USERNAME = "user"

INDEX_YML = """
project:
  id: proj
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
    db.ensure_project(PROJECT_ID)
    db.save_project_files(
        PROJECT_ID,
        {"index.yml": INDEX_YML.encode("utf-8"), "legal/terms.md": TERMS_MD},
        {"index.yml": "text/yaml", "legal/terms.md": "text/markdown"},
    )
    db.publish_project(PROJECT_ID)  # published_revision = 0
    return ProjectService(db)


def _chat_service_for(db, project_service: ProjectService) -> ChatService:
    ai_service = FakeAiService()
    metric_service = MetricService(db, project_service)
    job_service = make_test_job_service(db)
    actuator_factory = make_test_actuator_factory(db, job_service)
    tracking_service = TrackingService(db, project_service, metric_service, actuator_factory)
    return ChatService(
        ai_service=ai_service,
        ai_test_service=ai_service,
        project_service=project_service,
        db=db,
        session_manager=ChatSessionManager(db),
        tracking_service=tracking_service,
        metric_service=metric_service,
        job_service=job_service,
        actuator_factory=actuator_factory,
    )


def test_accept_legal_terms_resolves_pending_even_with_a_diverged_draft(db, project_service):
    # An unrelated draft edit forks every Archive row (including
    # legal/terms.md) to revision 1, identical content but a new row id —
    # exactly what happened to "TTM prototype" in production
    # (revision=1, published_revision=0, 5 draft edits).
    db.save_project_files(
        PROJECT_ID, {"index.yml": INDEX_YML.encode("utf-8")}, {"index.yml": "text/yaml"}
    )
    assert db.get_project_revision(PROJECT_ID) != db.get_project_published_revision(PROJECT_ID)

    assert project_service.legal_terms_pending(USERNAME, PROJECT_ID) is True

    project_service.accept_legal_terms(USERNAME, PROJECT_ID)

    assert project_service.legal_terms_pending(USERNAME, PROJECT_ID) is False


def test_accept_legal_terms_still_asks_again_if_the_published_terms_later_change(db, project_service):
    project_service.accept_legal_terms(USERNAME, PROJECT_ID)
    assert project_service.legal_terms_pending(USERNAME, PROJECT_ID) is False

    db.save_project_files(
        PROJECT_ID, {"legal/terms.md": b"# Terms\n\nNew content.\n"}, {"legal/terms.md": "text/markdown"}
    )
    db.publish_project(PROJECT_ID)  # published_revision advances to the new terms

    assert project_service.legal_terms_pending(USERNAME, PROJECT_ID) is True


async def test_an_already_open_session_is_never_blocked_by_terms_published_since(db, project_service):
    """The WhatsApp/web production bug this guards against: republishing
    anything after a user already has a live session open must never
    retroactively ask that user to accept terms mid-conversation — only a
    session about to be *created* is pinned to whatever's newly published
    (see ChatService._get_current_session_if_any_or_create_new_of_type)."""
    project_service.accept_legal_terms(USERNAME, PROJECT_ID)
    chat_service = _chat_service_for(db, project_service)

    first = await chat_service.get_current_session_if_any_or_create_new(None)
    assert first.get("legal_terms_pending") is not True
    session_id = first["id"]

    db.save_project_files(PROJECT_ID, {"index.yml": INDEX_YML.encode("utf-8")}, {"index.yml": "text/yaml"})
    db.publish_project(PROJECT_ID)
    # Sanity: a brand new session would indeed be gated by this — the
    # already-open one below just isn't.
    assert project_service.legal_terms_pending(USERNAME, PROJECT_ID) is True

    second = await chat_service.get_current_session_if_any_or_create_new(None)
    assert second.get("legal_terms_pending") is not True
    assert second["id"] == session_id


async def test_a_brand_new_session_is_still_blocked_by_currently_pending_terms(db, project_service):
    chat_service = _chat_service_for(db, project_service)

    payload = await chat_service.get_current_session_if_any_or_create_new(None)

    assert payload.get("legal_terms_pending") is True
