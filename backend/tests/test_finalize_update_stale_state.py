from __future__ import annotations

from datetime import datetime

import pytest

from conftest import parse_sse_result
from session import Session

pytestmark = pytest.mark.regression

TWO_STATE_YML = (
    "project:\n  id: proj\n"
    "init-action:\n  target: a\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: go\n"
    "        ui-label: Go\n"
    "        ui-button: Go\n"
    "        target: b\n"
    "  b:\n"
    "    contextual-prompt: there\n"
)

YML_WITHOUT_B = "project:\n  id: proj\ninit-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


def _upload_and_reach_b(client):
    resp = client.post("/api/projects/upload", content=TWO_STATE_YML.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]

    session = client.get("/api/chat/session").json()
    action_resp = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "go"})
    assert action_resp.status_code == 200, action_resp.text
    return project_id, session["id"]


def test_editing_the_current_users_stale_state_never_touches_another_users_live_session(client, app_db):
    project_id, my_session_id = _upload_and_reach_b(client)

    app_db.set_active_project_id(project_id, "bob")
    with Session().impersonate("bob"):
        bob_session = client.get("/api/chat/session").json()
        bob_action_resp = client.post(f"/api/chat/sessions/{bob_session['id']}/action", json={"action_name": "go"})
        assert bob_action_resp.status_code == 200, bob_action_resp.text

    resp = client.put("/api/projects/proj/files/index.yml", content=YML_WITHOUT_B.encode())
    assert resp.status_code == 200, resp.text

    assert app_db.get_chat_session(my_session_id) is None
    assert app_db.get_chat_session(bob_session["id"]) is not None
    assert app_db.get_chat_session(bob_session["id"])["end_state"] == "b"


def test_editing_the_current_users_stale_state_never_touches_an_imported_session(client, app_db):
    project_id, _ = _upload_and_reach_b(client)
    revision = app_db.get_project_published_revision(project_id)
    imported_id = app_db.create_chat_session(
        "someone-else", project_id, revision,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="b", end_state="b", type="imported",
    )

    resp = client.put("/api/projects/proj/files/index.yml", content=YML_WITHOUT_B.encode())
    assert resp.status_code == 200, resp.text

    assert client.get("/api/projects/proj/sessions").json() == []
    assert app_db.get_chat_session(imported_id) is not None


def test_editing_a_stale_state_deletes_the_current_users_own_test_session(client, app_db):
    project_id, _ = _upload_and_reach_b(client)
    test_session = client.post(f"/api/projects/{project_id}/test-sessions").json()
    action_resp = client.post(f"/api/chat/sessions/{test_session['id']}/action", json={"action_name": "go"})
    assert action_resp.status_code == 200, action_resp.text

    resp = client.put("/api/projects/proj/files/index.yml", content=YML_WITHOUT_B.encode())
    assert resp.status_code == 200, resp.text

    assert app_db.get_chat_session(test_session["id"]) is None
