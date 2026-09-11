from __future__ import annotations

import json

import pytest

from automaton.automaton import Action, Automaton, Signal, State, _TaskEval
from automaton.scope import EvaluationScope
from conftest import FakeAiService, parse_sse_result, run_pending_tasks
from db import Db
from tracking.actuators.actuator_set import FakeTaskNamespace, LiveTaskNamespace

pytestmark = pytest.mark.contract


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
        general_attachments={},
        autotracking_on_ai_message=False,
    )


def test_task_prompt_sends_an_isolated_single_turn():
    ai_service = FakeAiService()
    task_namespace = FakeTaskNamespace().with_ai_service(ai_service)

    text = task_namespace.prompt("Translate to Catalan: hello.")

    assert text == "Fake AI reply."
    system_prompt, history = ai_service.calls[0]
    assert system_prompt == ""
    assert history == [{"role": "user", "content": "Translate to Catalan: hello."}]


def test_task_prompt_leaves_no_message_persisted(db: Db):
    db.ensure_project("proj")
    session_id = db.create_chat_session("tester", "proj", revision=0, type="test")
    ai_service = FakeAiService()
    task_namespace = FakeTaskNamespace().with_ai_service(ai_service)

    task_namespace.prompt("Say hi.")

    assert db.get_messages(session_id) == []


def test_task_namespace_prompt_returns_empty_string_with_no_bound_context():
    assert FakeTaskNamespace().prompt("Say hi.") == ""
    assert LiveTaskNamespace(dispatcher=None).prompt("Say hi.") == ""


def test_with_ai_service_never_mutates_the_original_instance():
    ai_service = FakeAiService()
    original = FakeTaskNamespace()

    bound = original.with_ai_service(ai_service)

    assert bound.prompt("Say hi.") == "Fake AI reply."
    assert original.prompt("Say hi.") == ""


def test_task_can_compose_prompt_with_send_mail():
    """task.prompt's own reply text is usable as another task.* call's
    own argument, same "one statement, several namespaced calls nested"
    shape task scripts always supported — celebrate/notify moved to
    chat (see test_on_exit_chat_switch.py), so task.send_mail's own
    fake-mode report (see FakeTaskNamespace) is what's observable here
    now: its `to` argument is embedded verbatim."""
    ai_service = FakeAiService()
    automaton = _automaton()
    task_namespace = FakeTaskNamespace().with_ai_service(ai_service)

    scope = EvaluationScope({"task": task_namespace}, automaton=automaton, state_key="a")
    result = _TaskEval(names=scope).eval(
        "task.send_mail(task.prompt('Summarize the situation.'), 'note')"
    )

    message = "send_mail(to='Fake AI reply.') — Run actuators is off, no email was sent."
    assert result == f'notify({json.dumps("Task (test)")}, {json.dumps(message)})'


@pytest.mark.regression
def test_task_prompt_fires_through_the_real_app_end_to_end(client, app):
    """End-to-end: the real upload/build/manual-action pipeline, exercising
    a `task:` script's own task.prompt() call, hibernated as an
    ActionTask and run on a worker (see tracking/actuators/action_task.py)
    — its own reply text reaches the browser only via task.send_mail's
    fake-mode report now (see this file's own test_task_can_compose_prompt_with_send_mail
    for why), never a bare task.notify(...) — that moved to chat, an
    on-exit-only namespace a `task:` script can't reach at all."""
    yml = (
        "project:\n  id: prompt_proj\n"
        "init-action:\n  target: a\n"
        "states:\n"
        "  a:\n"
        "    contextual-prompt: hi\n"
        "    actions:\n"
        "      - name: go\n"
        "        target: b\n"
        "        task: task.send_mail(task.prompt('Summarize the situation.'), 'note')\n"
        "  b:\n"
        "    contextual-prompt: there\n"
    )
    resp = client.post("/api/skills/platform/projects/upload", content=yml.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    client.put(f"/api/skills/platform/projects/{project_id}/activate")
    client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})

    # A test/draft session, deliberately — "Run actuators" defaults off
    # there (see TaskNamespaceFactory.for_session), which is what makes
    # task.send_mail's own report observable at all: a live session
    # would really try to dial the (dummy, unreachable) SMTP config
    # instead (see test_action_task.py's own module docstring).
    session = client.post(f"/api/skills/platform/projects/{project_id}/test-sessions").json()
    action_response = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "go"})

    assert action_response.status_code == 200, action_response.text
    assert "task" not in action_response.json()
    # The model call runs in the task, off the request; its
    # result reaches the browser as a notification frame.
    frames = run_pending_tasks(app)
    message = "send_mail(to='Fake AI reply.') — Run actuators is off, no email was sent."
    assert frames == [{"type": "ui.notification", "task": f'notify({json.dumps("Task (test)")}, {json.dumps(message)})'}]
