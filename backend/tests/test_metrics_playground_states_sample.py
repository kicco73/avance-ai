"""Exercises samples/Metrics Playground (states).zip: same signal/metric
triggers as Metrics Playground.zip, but each lands on its own dedicated
final state instead of looping back to "engaged".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import parse_sse_result

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "projects"


def _upload_and_activate(client):
    content = (SAMPLES_DIR / "Metrics Playground (states).zip").read_bytes()
    response = client.post("/api/projects/upload", content=content, headers={"Content-Type": "application/zip"})
    assert response.status_code == 200, response.text
    project_id = parse_sse_result(response)["project_id"]
    response = client.put(f"/api/projects/{project_id}/activate")
    assert response.status_code == 200, response.text
    response = client.post(f"/api/projects/{project_id}/publish", json={})
    assert response.status_code == 200, response.text
    return project_id


@pytest.mark.contract
def test_the_sample_loads_and_starts_at_lobby(client):
    _upload_and_activate(client)

    session = client.get("/api/chat/session").json()

    assert session["start_state"] == "lobby"


@pytest.mark.regression
def test_firing_notice_mood_actually_moves_to_its_own_dedicated_state(client):
    _upload_and_activate(client)
    session = client.get("/api/chat/session").json()
    move = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "warm_up"})
    assert move.json()["state"]["key"] == "engaged"

    # Manual invocation (like clicking the button) never checks the
    # trigger — see Automaton.move — so this exercises the real target
    # state without needing the "mood" signal to actually be >= 70.
    response = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "notice_mood"})

    assert response.status_code == 200
    assert response.json()["state"]["key"] == "mood_reached"
    # Unlike the self-looping variant, this is a genuine final state.
    assert response.json()["state"]["final"] is True
