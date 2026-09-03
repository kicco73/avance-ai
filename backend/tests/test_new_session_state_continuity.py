"""A new live session must resume wherever this user's conversation
already is — never replay init-action, which would both jump the visible
state back to the start and re-fire whatever on-enter the project defines
for it. A new test/draft session is the opposite: always a fresh run
through init-action, regardless of where a previous draft session left off.
"""
from __future__ import annotations

import pytest

from conftest import parse_sse_result

pytestmark = pytest.mark.contract

YML = (
    "project:\n  id: proj\n"
    "init-action:\n  target: a\n  on-enter: actuator.celebrate()\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: go\n"
    "        target: b\n"
    "  b:\n"
    "    contextual-prompt: there\n"
)


def _upload_and_publish(client):
    resp = client.post("/api/projects/upload", content=YML.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    resp = client.post(f"/api/projects/{project_id}/publish", json={})
    assert resp.status_code == 200, resp.text
    return project_id


def test_new_live_session_resumes_the_users_current_state_not_init(client):
    _upload_and_publish(client)
    session = client.get("/api/chat/session").json()
    assert session["state"]["key"] == "a"

    resp = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "go"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"]["key"] == "b"

    resp = client.post("/api/chat/sessions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["start_state"] == "b"
    assert "on-enter" not in body

    # A brand-new session has no Tracking rows of its own yet — re-fetching
    # it (exactly what the frontend's loadMessages() does right after
    # creating it) must still read "b" off the session's own persisted
    # start_state, not fall back to init for lack of a transition to read.
    resp = client.get(f"/api/chat/session?session_id={body['id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"]["key"] == "b"


CHATLESS_FINAL_YML = (
    "project:\n  id: proj\n"
    "init-action:\n  target: a\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: go\n"
    "        target: crisis\n"
    "  crisis:\n"
    "    contextual-prompt: bye\n"
    "    chat: false\n"
    "    actions: []\n"
)


def test_new_live_session_from_a_chatless_final_state_still_resumes_there(client):
    resp = client.post("/api/projects/upload", content=CHATLESS_FINAL_YML.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    resp = client.post(f"/api/projects/{project_id}/publish", json={})
    assert resp.status_code == 200, resp.text

    session = client.get("/api/chat/session").json()
    client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "go"})

    resp = client.post("/api/chat/sessions")
    assert resp.status_code == 200, resp.text
    new_session_id = resp.json()["id"]

    resp = client.get(f"/api/chat/session?session_id={new_session_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"]["key"] == "crisis"


def test_new_test_session_still_restarts_at_init_every_time(client, app_db):
    project_id = _upload_and_publish(client)
    resp = client.post(f"/api/projects/{project_id}/test-sessions")
    assert resp.status_code == 200, resp.text
    first = resp.json()
    assert first["start_state"] == "a"
    # init-action's on-enter fires as a task, never inside this response.
    assert "on-enter" not in first
    assert [t["payload"]["script"].strip() for t in app_db.list_tasks()] == ["actuator.celebrate()"]

    resp = client.post(f"/api/chat/sessions/{first['id']}/action", json={"action_name": "go"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"]["key"] == "b"

    resp = client.post(f"/api/projects/{project_id}/test-sessions")
    assert resp.status_code == 200, resp.text
    second = resp.json()
    assert second["start_state"] == "a"
    assert "on-enter" not in second
    assert [t["payload"]["script"].strip() for t in app_db.list_tasks()] == ["actuator.celebrate()"] * 2
