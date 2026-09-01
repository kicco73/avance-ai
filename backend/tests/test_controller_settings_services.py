from __future__ import annotations

import pytest


@pytest.mark.contract
def test_get_services_returns_the_configured_snapshot_verbatim(client):
    response = client.get("/api/settings/services")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"chat", "testing", "ai", "talk", "listen", "database"}
    assert body["database"]["url"] == "sqlite:///test.db"
    assert body["ai"]["providers"][0]["driver"] == "fake"


@pytest.mark.contract
def test_wipe_all_live_sessions_deletes_sessions_across_every_project(client, hello_project):
    session_id = client.get("/api/chat/session").json()["id"]
    assert client.get(f"/api/chat/sessions/{session_id}/messages").status_code == 200

    response = client.post("/api/settings/database/wipe-live-sessions")

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert client.get(f"/api/chat/sessions/{session_id}/messages").status_code == 404

    # The project definition itself is untouched — only its live sessions.
    assert client.get(f"/api/projects/{hello_project}").status_code == 200
