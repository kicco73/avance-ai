"""ChatService's own two guards against a broken/paused project (see
ChatService._ensure_project_available/_get_automaton_and_state_or_raise_unsupported):
an already-open session must reject a turn/manual action on a project
that's since gone unavailable (code="project_unavailable"), and a session
pinned to a stored revision that no longer builds must degrade to a 409
(code="session_revision_unsupported") instead of whatever the build
failure itself would have raised.
"""
from __future__ import annotations

from http import HTTPStatus

import pytest

from automaton.automaton import Action, Automaton, State
from chat.chat_service import ChatService
from chat.errors import ChatServiceError
from chat.session_manager import ChatSessionManager
from conftest import FakeAiService, make_test_actuator_factory, make_test_job_service
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService

PROJECT_ID = "proj"


def _automaton() -> Automaton:
    action = Action(name="advance", ui_label="Advance", ui_button="Advance", target="a")
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[action])
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a},
        general_prompt="", signals=[], attachments={}, general_attachments={}, autotracking_on_ai_message=False,
    )


class _FakeProjectService:
    """Same shape as test_chat_service_manual_actions.py's own fake, plus
    two knobs this file's tests actually flip mid-test: `available`
    (get_project_availability's own return) and `session_lookup_error`
    (get_automaton_and_state_for_session raises this instead of resolving)."""

    def __init__(self, automaton: Automaton) -> None:
        self._automaton = automaton
        self.available: tuple[bool, str | None] = (False, None)
        self.session_lookup_error: Exception | None = None

    def get_automaton_and_state(self, project_id: str, type: str = 'live', username: str | None = None):
        return self._automaton, self._automaton.states["a"]

    def get_automaton(self, project_id: str, revision: int) -> Automaton:
        return self._automaton

    def get_automaton_and_state_for_session(self, session_id: int):
        if self.session_lookup_error is not None:
            raise self.session_lookup_error
        return self._automaton, self._automaton.states["a"]

    def get_automaton_for_session(self, session_id: int) -> Automaton:
        return self._automaton

    def get_active_project_id(self) -> str:
        return PROJECT_ID

    def get_published_revision(self, project_id: str) -> int:
        return 0

    def get_draft_revision(self, project_id: str) -> int:
        return 0

    def legal_terms_pending(self, username: str, project_id: str) -> bool:
        return False

    def get_project_availability(self, project_id: str):
        return self.available

    def apply_manual_action(self, action_name: str, session_id: int):
        state_payload = self._automaton.get_state_payload(self._automaton.states["a"])
        return state_payload, self._automaton.states["a"].actions[0], "a"


def _chat_service(db) -> tuple[ChatService, _FakeProjectService]:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    ai_service = FakeAiService()
    project_service = _FakeProjectService(_automaton())
    metric_service = MetricService(db, project_service)
    job_service = make_test_job_service(db)
    actuator_factory = make_test_actuator_factory(db, job_service)
    tracking_service = TrackingService(db, project_service, metric_service, actuator_factory)
    chat_service = ChatService(
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
    return chat_service, project_service


async def test_process_turn_rejects_a_turn_on_a_now_paused_project(db):
    chat_service, project_service = _chat_service(db)
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    project_service.available = (True, "index.yml no longer builds — nope")

    with pytest.raises(ChatServiceError) as exc_info:
        await chat_service.process_turn(session["id"], "hi")

    assert exc_info.value.status_code == HTTPStatus.CONFLICT
    assert exc_info.value.code == "project_unavailable"
    assert "no longer builds" in exc_info.value.message


async def test_apply_manual_action_rejects_on_a_now_paused_project(db):
    chat_service, project_service = _chat_service(db)
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    project_service.available = (True, "Manually paused.")

    with pytest.raises(ChatServiceError) as exc_info:
        await chat_service.apply_manual_action("advance", session["id"])

    assert exc_info.value.status_code == HTTPStatus.CONFLICT
    assert exc_info.value.code == "project_unavailable"


async def test_get_state_for_session_reports_an_unsupported_pinned_revision(db):
    chat_service, project_service = _chat_service(db)
    session = await chat_service.get_current_session_if_any_or_create_new(None)
    # The project itself is fine (published builds) — only *this* session's
    # own pinned revision (an old, since-superseded one) doesn't anymore.
    project_service.session_lookup_error = ValueError("Project 'proj', stored revision 0: index.yml no longer builds — nope")

    with pytest.raises(ChatServiceError) as exc_info:
        chat_service.get_state_for_session(session["id"])

    assert exc_info.value.status_code == HTTPStatus.CONFLICT
    assert exc_info.value.code == "session_revision_unsupported"
    assert "revision 0" in exc_info.value.message


async def test_a_healthy_session_on_an_available_project_is_unaffected(db):
    chat_service, project_service = _chat_service(db)
    session = await chat_service.get_current_session_if_any_or_create_new(None)

    state = chat_service.get_state_for_session(session["id"])

    assert state["key"] == "a"
