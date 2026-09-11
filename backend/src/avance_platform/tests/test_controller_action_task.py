"""POST /api/action fires the action's own task (its snippets reach the
browser over the websocket as a background ActionTask, never in this
response) and, separately, its own on-exit script — including any
chat.* calls, pushed synchronously, in this same request, never
hibernated. Both are the action's own, not anything read off the
destination state — two actions landing on the same state can disagree
on either.
"""
from __future__ import annotations

import pytest

from system.ws_notifications import WsNotifications
from conftest import FakeWebSocket, parse_sse_result

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
    "        on-exit: chat.celebrate()\n"
    "  b:\n"
    "    contextual-prompt: there\n"
)


def _upload_and_get_session(client):
    resp = client.post("/api/skills/platform/projects/upload", content=YML.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    resp = client.put(f"/api/skills/platform/projects/{project_id}/activate")
    assert resp.status_code == 200, resp.text
    resp = client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})
    assert resp.status_code == 200, resp.text
    return client.get("/api/chat/session").json()


def _attach_websocket(app, username: str) -> FakeWebSocket:
    """Same idea as conftest.run_pending_tasks, but wired up before the
    action fires — on-exit's own chat.* push happens synchronously,
    inside the /action request itself, never through the job queue."""
    websocket = FakeWebSocket()
    ws_notifications = WsNotifications(auth_service=None)
    ws_notifications._connections[username] = [websocket]
    return websocket


def test_manual_action_pushes_its_on_exits_own_chat_snippets_synchronously(client, app, app_db):
    session = _upload_and_get_session(client)
    websocket = _attach_websocket(app, session["username"])

    resp = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "go-loud"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"]["key"] == "b"
    assert "task" not in body
    # Never hibernated as a Task: on-exit's own chat.* runs inline, not
    # through the job queue at all (see TrackingEngine.apply_action_env).
    assert app_db.list_tasks() == []
    assert websocket.sent == [{"type": "ui.notification", "task": "celebrate()"}]


def test_manual_action_without_on_exit_reports_none_even_for_the_same_target_state(client, app_db):
    session = _upload_and_get_session(client)

    resp = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "go-quiet"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"]["key"] == "b"
    assert "task" not in body
    assert app_db.list_tasks() == []


def test_state_payload_never_carries_on_exit_itself(client):
    """on-exit is per-action, never present on the state payload itself."""
    session = _upload_and_get_session(client)

    resp = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "go-loud"})

    assert "on-exit" not in resp.json()["state"]
    for action in resp.json()["state"]["actions"]:
        assert "on-exit" in action  # present per outgoing action instead


def test_get_state_has_no_task_since_nothing_just_fired(client):
    _upload_and_get_session(client)

    resp = client.get("/api/skills/platform/state")

    assert resp.status_code == 200
    assert "task" not in resp.json()
