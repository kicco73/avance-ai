"""POST /api/action schedules the fired action's own on_enter as a task
(its snippets reach the browser over the websocket, never in this
response), and it is the action's, not anything read off the destination state —
two actions landing on the same state can disagree on it.
"""
from __future__ import annotations

import pytest

from conftest import run_on_enter_tasks, parse_sse_result

pytestmark = pytest.mark.contract

YML = (
    "project:\n  id: proj\n"
    "init-action:\n  target: a\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: go-quiet\n"
    "        target: b\n"
    "      - name: go-loud\n"
    "        target: b\n"
    "        on-enter: actuator.celebrate()\n"
    "  b:\n"
    "    contextual-prompt: there\n"
)


def _upload_and_get_session(client):
    resp = client.post("/api/projects/upload", content=YML.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    resp = client.put(f"/api/projects/{project_id}/activate")
    assert resp.status_code == 200, resp.text
    resp = client.post(f"/api/projects/{project_id}/publish", json={})
    assert resp.status_code == 200, resp.text
    return client.get("/api/chat/session").json()


def test_manual_action_reports_the_fired_actions_own_on_enter(client, app, app_db):
    session = _upload_and_get_session(client)

    resp = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "go-loud"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"]["key"] == "b"
    assert "on-enter" not in body
    (task,) = app_db.list_tasks()
    assert task["payload"]["script"].strip() == "actuator.celebrate()"
    assert task["payload"]["action_name"] == "go-loud"
    assert run_on_enter_tasks(app) == [{"type": "notification", "on-enter": "celebrate()"}]


def test_manual_action_without_on_enter_reports_none_even_for_the_same_target_state(client, app_db):
    session = _upload_and_get_session(client)

    resp = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "go-quiet"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"]["key"] == "b"
    assert "on-enter" not in body
    assert app_db.list_tasks() == []


def test_state_payload_never_carries_on_enter_itself(client):
    """on-enter is per-action, never present on the state payload itself."""
    session = _upload_and_get_session(client)

    resp = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "go-loud"})

    assert "on-enter" not in resp.json()["state"]
    for action in resp.json()["state"]["actions"]:
        assert "on-enter" in action  # present per outgoing action instead


def test_get_state_has_no_on_enter_since_nothing_just_fired(client):
    _upload_and_get_session(client)

    resp = client.get("/api/state")

    assert resp.status_code == 200
    assert "on-enter" not in resp.json()
