"""POST /api/chat/sessions/{id}/truncate ("Restart from here",
ChatService.truncate_session) — exercises the HTTP surface: ownership,
response shape, and an end-to-end scenario against a real automaton.
"""
from __future__ import annotations

from pathlib import Path

import pytest

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "projects"


@pytest.mark.contract
def test_truncate_rejects_an_unknown_session(client, hello_project):
    response = client.post("/api/chat/sessions/999999/truncate", json={"timestamp": "2026-01-01T00:00:00+00:00"})
    assert response.status_code == 404


@pytest.mark.contract
def test_truncate_rejects_someone_elses_session(client, hello_project):
    session = client.get("/api/chat/session").json()
    # Reassign ownership directly — no endpoint exists to create another
    # user's session.
    from db.models import ChatSession

    ChatSession.update(username="someone-else").where(ChatSession.id == session["id"]).execute()

    response = client.post(f"/api/chat/sessions/{session['id']}/truncate", json={"timestamp": "2026-01-01T00:00:00+00:00"})
    assert response.status_code == 404


@pytest.mark.contract
def test_truncate_rejects_a_malformed_timestamp(client, hello_project):
    session = client.get("/api/chat/session").json()

    response = client.post(f"/api/chat/sessions/{session['id']}/truncate", json={"timestamp": "not-a-timestamp"})

    assert response.status_code == 400


@pytest.mark.contract
def test_truncate_response_shape_is_a_bare_state_payload(client, hello_project):
    """Truncate returns a bare StatePayload, unlike GET /api/state's
    superset. It never fires init-action, so it carries no "on-enter"
    key, unlike reset's response."""
    session = client.get("/api/chat/session").json()

    response = client.post(
        f"/api/chat/sessions/{session['id']}/truncate", json={"timestamp": "2099-01-01T00:00:00+00:00"}
    )
    reset_response = client.post(f"/api/projects/{hello_project}/test-sessions/reset")

    assert response.status_code == 200
    assert "on-enter" not in response.json()
    assert set(response.json().keys()) == set(reset_response.json().keys()) - {"on-enter"}


@pytest.mark.regression
def test_truncate_deletes_trailing_turns_and_rolls_the_live_state_back(client):
    """End-to-end: move a real automaton away from its initial state via
    a manual action, then truncate at that transition's timestamp — the
    transition and the state it produced must both be gone."""
    content = (SAMPLES_DIR / "Aprendr català.zip").read_bytes()
    resp = client.put("/api/projects/cat", content=content, headers={"Content-Type": "application/zip"})
    assert resp.status_code == 200, resp.text
    client.put("/api/projects/cat/activate")
    client.post("/api/projects/cat/publish", json={})

    session = client.get("/api/chat/session").json()
    assert session["start_state"] == "welcome"

    action_response = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "unit-subjuntive"})
    assert action_response.status_code == 200
    moved_state = action_response.json()["state"]["key"]
    assert moved_state != "welcome"

    signals = client.get(f"/api/chat/sessions/{session['id']}/signals").json()
    transition = next(s for s in signals if s["new_state"] == moved_state)

    truncate_response = client.post(
        f"/api/chat/sessions/{session['id']}/truncate", json={"timestamp": transition["timestamp"]}
    )
    assert truncate_response.status_code == 200
    assert truncate_response.json()["key"] == "welcome"

    assert client.get("/api/state").json()["key"] == "welcome"
    remaining_signals = client.get(f"/api/chat/sessions/{session['id']}/signals").json()
    assert all(s["new_state"] != moved_state for s in remaining_signals)
