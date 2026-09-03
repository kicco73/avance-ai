from __future__ import annotations

import json
from pathlib import Path

import pytest

from automaton.automaton import Action, Automaton, Signal, State, _OnEnterEval
from automaton.scope import EvaluationScope
from conftest import FakeAiService, parse_sse_result, run_on_enter_tasks
from db import Db
from tracking.actuators.actuator_set import FakeActuatorSet, LiveActuatorSet
from tracking.actuators.prompt_context import PromptContext
from tracking.env import Env

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


def _session_id(db: Db, project_id: str = "proj") -> int:
    db.ensure_project(project_id)
    return db.create_chat_session("tester", project_id, revision=0, type="test")


async def test_prompt_context_returns_the_aggregated_text_and_replaces_contextual_prompt(db: Db):
    ai_service = FakeAiService()
    automaton = _automaton()
    state = automaton.states["a"]
    session_id = _session_id(db)
    context = PromptContext(ai_service, db, Env(), automaton, state, session_id)

    text = context.run("Say hi to the user.")

    assert text == "Fake AI reply."
    system_prompt, _ = ai_service.calls[0]
    assert "General instructions." in system_prompt
    assert "Say hi to the user." in system_prompt
    # The passed-in prompt stands in for the state's own contextual-prompt
    # — it is never also included (see PromptContext._build_system_prompt).
    assert "You are in A." not in system_prompt


async def test_prompt_context_sends_env_and_triggerable_signal_definitions(db: Db):
    ai_service = FakeAiService()
    automaton = _automaton()
    state = automaton.states["a"]
    action = Action(name="go", ui_label="Go", ui_button="Go", target="a", trigger="signal.mood >= 50")
    state.actions.append(action)
    session_id = _session_id(db)
    env = Env(stored={"favorite_color": "blue"})
    context = PromptContext(ai_service, db, env, automaton, state, session_id)

    context.run("Extra prompt text.")

    system_prompt, _ = ai_service.calls[0]
    assert "mood" in system_prompt
    assert "favorite_color: blue" in system_prompt


async def test_prompt_context_leaves_no_message_persisted(db: Db):
    ai_service = FakeAiService()
    automaton = _automaton()
    state = automaton.states["a"]
    session_id = _session_id(db)
    context = PromptContext(ai_service, db, Env(), automaton, state, session_id)

    context.run("Say hi.")

    assert db.get_messages(session_id) == []


def test_actuator_set_prompt_returns_empty_string_with_no_bound_context():
    assert FakeActuatorSet().prompt("Say hi.") == ""
    assert LiveActuatorSet(notification_service=None, dispatcher=None).prompt("Say hi.") == ""


async def test_with_prompt_context_never_mutates_the_original_instance(db: Db):
    ai_service = FakeAiService()
    automaton = _automaton()
    state = automaton.states["a"]
    session_id = _session_id(db)
    context = PromptContext(ai_service, db, Env(), automaton, state, session_id)
    original = FakeActuatorSet()

    bound = original.with_prompt_context(context)

    assert bound.prompt("Say hi.") == "Fake AI reply."
    assert original.prompt("Say hi.") == ""


async def test_on_enter_can_compose_prompt_with_notify(db: Db):
    ai_service = FakeAiService()
    automaton = _automaton()
    state = automaton.states["a"]
    session_id = _session_id(db)
    context = PromptContext(ai_service, db, Env(), automaton, state, session_id)
    actuator = FakeActuatorSet().with_prompt_context(context)

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
