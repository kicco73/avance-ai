from __future__ import annotations

import pytest

from session import Session

pytestmark = pytest.mark.contract


def test_metrics_history_spans_every_session_chronologically(client, app_db, hello_project):
    app_db.set_active_project_name(hello_project, "alice")
    with Session().impersonate("alice"):
        older = client.get("/api/chat/session").json()
        client.post(f"/api/chat/sessions/{older['id']}/messages", json={"message": "hi"})
        newer = client.post("/api/chat/sessions").json()
        client.post(f"/api/chat/sessions/{newer['id']}/messages", json={"message": "hello again"})

    response = client.get("/api/projects/hello/users/alice/metrics-history")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["timestamp"] <= body[1]["timestamp"]
    for entry in body:
        assert "engagement" in entry["values"]
        assert 0.0 <= entry["values"]["engagement"] <= 100.0


def test_metrics_history_is_scoped_to_the_given_user_and_project(client, app_db, hello_project):
    app_db.set_active_project_name(hello_project, "alice")
    with Session().impersonate("alice"):
        client.get("/api/chat/session")

    app_db.set_active_project_name(hello_project, "carol")
    with Session().impersonate("carol"):
        session = client.get("/api/chat/session").json()
        client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"})

    alice_body = client.get("/api/projects/hello/users/alice/metrics-history").json()
    carol_body = client.get("/api/projects/hello/users/carol/metrics-history").json()

    assert len(alice_body) == 1
    assert len(carol_body) == 1
    assert alice_body[0]["values"]["engagement"] != carol_body[0]["values"]["engagement"]
