"""ChatService's own manual_actions field on every state payload reaching
a client with a known session (see automaton.manual_actions_for) — a live
session always excludes triggered actions, a test session only while its
own auto-tracking toggle is on.
"""
from __future__ import annotations

from automaton.automaton import Action, Automaton, State
from chat.chat_service import ChatService
from chat.session_manager import ChatSessionManager
from conftest import FakeAiService, make_test_actuator_factory, make_test_job_service
from metrics.metric_service import MetricService
from tracking.tracking_service import TrackingService

PROJECT_ID = "proj"


def _automaton() -> Automaton:
    manual_action = Action(name="manual", ui_label="Manual", ui_button="Manual", target="a")
    triggered_action = Action(name="auto", ui_label="Auto", ui_button="Auto", target="a", trigger="True")
    state_a = State(key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[manual_action, triggered_action])
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a},
        general_prompt="", signals=[], attachments={}, general_attachments={}, autotracking_on_ai_message=False,
    )


class _FakeProjectService:
    def __init__(self, automaton: Automaton) -> None:
        self._automaton = automaton

    def get_automaton_and_state(self, project_id: str, type: str = 'live', username: str | None = None):
        return self._automaton, self._automaton.states["a"]

    def get_automaton(self, project_id: str, revision: int) -> Automaton:
        return self._automaton

    def get_automaton_and_state_for_session(self, session_id: int):
        return self._automaton, self._automaton.states["a"]

    def get_active_project_id(self) -> str:
        return PROJECT_ID

    def get_published_revision(self, project_id: str) -> int:
        return 0

    def get_draft_revision(self, project_id: str) -> int:
        return 0

    def legal_terms_pending(self, username: str, project_id: str) -> bool:
        return False

    def get_project_availability(self, project_id: str):
        return (False, None)


def _chat_service(db) -> ChatService:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    ai_service = FakeAiService()
    project_service = _FakeProjectService(_automaton())
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


async def test_live_session_always_excludes_triggered_actions(db):
    chat_service = _chat_service(db)

    session = await chat_service.get_current_session_if_any_or_create_new(None)

    names = {a["name"] for a in session["state"]["manual_actions"]}
    assert names == {"manual"}


async def test_test_session_excludes_triggered_actions_while_auto_tracking_is_on(db):
    chat_service = _chat_service(db)

    session = await chat_service.get_current_draft_session_if_any_or_create_new(None, PROJECT_ID)

    assert chat_service.is_auto_tracking_enabled(session["id"]) is True
    names = {a["name"] for a in session["state"]["manual_actions"]}
    assert names == {"manual"}


async def test_test_session_includes_triggered_actions_once_auto_tracking_is_off(db):
    chat_service = _chat_service(db)
    session = await chat_service.get_current_draft_session_if_any_or_create_new(None, PROJECT_ID)
    session_id = session["id"]

    chat_service.set_auto_tracking_enabled(session_id, False)
    state = chat_service.get_state_for_session(session_id)

    names = {a["name"] for a in state["manual_actions"]}
    assert names == {"manual", "auto"}


async def test_actions_field_itself_is_never_filtered(db):
    chat_service = _chat_service(db)

    session = await chat_service.get_current_session_if_any_or_create_new(None)

    names = {a["name"] for a in session["state"]["actions"]}
    assert names == {"manual", "auto"}
