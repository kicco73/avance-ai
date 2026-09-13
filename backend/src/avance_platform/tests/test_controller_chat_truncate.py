"""POST /api/core/sessions/{id}/truncate ("Restart from here",
TurnService.truncate_session) — exercises the HTTP surface: ownership,
response shape, and an end-to-end scenario against a real automaton.
"""
from __future__ import annotations

import pytest

from conftest import enter_chat, parse_sse_result, session_of
from system.web_session import WebSession

from conftest import SAMPLES_DIR


@pytest.mark.contract
def test_truncate_rejects_an_unknown_session(client, hello_project):
    response = client.post("/api/core/sessions/999999/truncate", json={"timestamp": "2026-01-01T00:00:00+00:00"})
    assert response.status_code == 404


@pytest.mark.contract
def test_truncate_rejects_someone_elses_session(client, hello_project):
    session_id = session_of(enter_chat(client, hello_project))
    # Reassign ownership directly — no endpoint exists to create another
    # user's session.
    from db.models import CoreSession

    CoreSession.update(username="someone-else").where(CoreSession.id == session_id).execute()

    # Only a plain "user" is denied — a supervisor owns every session (see
    # TurnService._owns_session), so this must downgrade the default fixture role.
    WebSession().role = "user"
    response = client.post(f"/api/core/sessions/{session_id}/truncate", json={"timestamp": "2026-01-01T00:00:00+00:00"})
    assert response.status_code == 404


@pytest.mark.contract
def test_truncate_rejects_a_malformed_timestamp(client, hello_project):
    session_id = session_of(enter_chat(client, hello_project))

    response = client.post(f"/api/core/sessions/{session_id}/truncate", json={"timestamp": "not-a-timestamp"})

    assert response.status_code == 400


@pytest.mark.contract
def test_truncate_response_shape_is_a_bare_state_payload(client, hello_project):
    """Truncate returns a bare StatePayload, unlike GET /api/core/state's
    superset. It never fires init-action, so it carries no "task"
    key, unlike reset's response."""
    session_id = session_of(enter_chat(client, hello_project))

    response = client.post(
        f"/api/core/sessions/{session_id}/truncate", json={"timestamp": "2099-01-01T00:00:00+00:00"}
    )
    reset_response = client.post(f"/api/skills/platform/projects/{hello_project}/test-sessions/reset")

    assert response.status_code == 200
    assert "task" not in response.json()
    assert set(response.json().keys()) == set(reset_response.json().keys()) - {"task"}


@pytest.mark.regression
def test_truncate_deletes_trailing_turns_and_rolls_the_live_state_back(client):
    """End-to-end: move a real automaton away from its initial state via
    a manual action, then truncate at that transition's timestamp — the
    transition and the state it produced must both be gone."""
    content = (SAMPLES_DIR / "Aprendr català.zip").read_bytes()
    resp = client.post("/api/skills/platform/projects/upload", content=content, headers={"Content-Type": "application/zip"})
    assert resp.status_code == 200, resp.text
    project_id = parse_sse_result(resp)["project_id"]
    client.post(f"/api/skills/platform/projects/{project_id}/activate")
    client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})

    session_id = session_of(enter_chat(client, project_id))
    sessions = client.get(f"/api/core/projects/{project_id}/sessions").json()
    assert next(s for s in sessions if s["id"] == session_id)["start_state"] == "welcome"

    action_response = client.post(f"/api/core/sessions/{session_id}/actions", json={"action_name": "unit-subjuntive"})
    assert action_response.status_code == 200
    moved_state = action_response.json()["state"]["key"]
    assert moved_state != "welcome"

    signals = client.get(f"/api/core/sessions/{session_id}/signals").json()
    transition = next(s for s in signals if s["new_state"] == moved_state)

    truncate_response = client.post(
        f"/api/core/sessions/{session_id}/truncate", json={"timestamp": transition["timestamp"]}
    )
    assert truncate_response.status_code == 200
    assert truncate_response.json()["key"] == "welcome"

    assert client.get("/api/core/state").json()["key"] == "welcome"
    remaining_signals = client.get(f"/api/core/sessions/{session_id}/signals").json()
    assert all(s["new_state"] != moved_state for s in remaining_signals)
