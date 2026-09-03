from __future__ import annotations

import json
from pathlib import Path

import pytest

from automaton.automaton import Action, Automaton, Signal, State, _OnEnterEval
from automaton.scope import EvaluationScope
from conftest import FakeAiService, parse_sse_result, run_on_enter_tasks
from db import Db
from tracking.actuators.actuator_set import FakeActuatorSet, LiveActuatorSet

pytestmark = pytest.mark.contract

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "projects"


def _automaton() -> Automaton:
    state = State(
        key="a", ui_label="A", final=False, contextual_prompt="You are in A.",
        attachments={}, actions=[],
    )
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"a": state},
        general_prompt="General instructions.",
        signals=[Signal(name="mood", ui_label="Mood", definition="How positive the tone is.")],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=False,
    )


def test_actuator_prompt_sends_an_isolated_single_turn():
    ai_service = FakeAiService()
    actuator = FakeActuatorSet().with_ai_service(ai_service)

    text = actuator.prompt("Translate to Catalan: hello.")

    assert text == "Fake AI reply."
    system_prompt, history = ai_service.calls[0]
    assert system_prompt == ""
    assert history == [{"role": "user", "content": "Translate to Catalan: hello."}]


def test_actuator_prompt_leaves_no_message_persisted(db: Db):
    db.ensure_project("proj")
    session_id = db.create_chat_session("tester", "proj", revision=0, type="test")
    ai_service = FakeAiService()
    actuator = FakeActuatorSet().with_ai_service(ai_service)

    actuator.prompt("Say hi.")

    assert db.get_messages(session_id) == []


def test_actuator_set_prompt_returns_empty_string_with_no_bound_context():
    assert FakeActuatorSet().prompt("Say hi.") == ""
    assert LiveActuatorSet(notification_service=None, dispatcher=None).prompt("Say hi.") == ""


def test_with_ai_service_never_mutates_the_original_instance():
    ai_service = FakeAiService()
    original = FakeActuatorSet()

    bound = original.with_ai_service(ai_service)

    assert bound.prompt("Say hi.") == "Fake AI reply."
    assert original.prompt("Say hi.") == ""


def test_on_enter_can_compose_prompt_with_notify():
    ai_service = FakeAiService()
    automaton = _automaton()
    actuator = FakeActuatorSet().with_ai_service(ai_service)

    scope = EvaluationScope({"actuator": actuator}, automaton=automaton, state_key="a")
    result = _OnEnterEval(names=scope).eval(
        "actuator.notify('Note', actuator.prompt('Summarize the situation.'))"
    )

    assert result == 'notify("Note", "Fake AI reply.")'


@pytest.mark.regression
def test_aprendr_catala_sample_fires_actuator_prompt_through_the_real_app(client, app):
    """End-to-end: the real upload/build/manual-action pipeline, exercising
    'grammar''s own on-enter (actuator.notify(..., actuator.prompt(...)))
    — the migration this sample project got when action-prompt was removed."""
    content = (SAMPLES_DIR / "Aprendr català.zip").read_bytes()
    resp = client.post("/api/projects/upload", content=content, headers={"Content-Type": "application/zip"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    client.put(f"/api/projects/{project_id}/activate")
    client.post(f"/api/projects/{project_id}/publish", json={})

    session = client.get("/api/chat/session").json()
    client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "unit-subjuntive"})
    client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "regulars"})
    action_response = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "grammar"})

    assert action_response.status_code == 200, action_response.text
    assert "on-enter" not in action_response.json()
    # The model call runs in the on-enter task, off the request; its
    # result reaches the browser as a notification frame.
    frames = run_on_enter_tasks(app)
    assert frames == [{"type": "notification", "on-enter": f'notify({json.dumps("Gramàtica")}, "Fake AI reply.")'}]
