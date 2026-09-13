"""Exercises samples/Metrics Playground (states).zip: same signal/metric
triggers as Metrics Playground.zip, but each lands on its own dedicated
final state instead of looping back to "engaged".
"""
from __future__ import annotations

import pytest

from conftest import enter_chat, parse_sse_result, session_of

from conftest import SAMPLES_DIR


def _upload_and_activate(client):
    content = (SAMPLES_DIR / "Metrics Playground (states).zip").read_bytes()
    response = client.post("/api/skills/platform/projects/upload", content=content, headers={"Content-Type": "application/zip"})
    assert response.status_code == 200, response.text
    project_id = parse_sse_result(response)["project_id"]
    response = client.post(f"/api/skills/platform/projects/{project_id}/activate")
    assert response.status_code == 200, response.text
    response = client.post(f"/api/skills/platform/projects/{project_id}/publish", json={})
    assert response.status_code == 200, response.text
    return project_id


@pytest.mark.contract
def test_the_sample_loads_and_starts_at_lobby(client):
    project_id = _upload_and_activate(client)

    info = next(frame for frame in enter_chat(client, project_id) if frame["type"] == "session.info")

    assert info["state"]["key"] == "lobby"


@pytest.mark.regression
def test_firing_notice_mood_actually_moves_to_its_own_dedicated_state(client):
    project_id = _upload_and_activate(client)
    session_id = session_of(enter_chat(client, project_id))
    move = client.post(f"/api/core/sessions/{session_id}/actions", json={"action_name": "warm_up"})
    assert move.json()["state"]["key"] == "engaged"

    # Manual invocation (like clicking the button) never checks the
    # trigger — see Automaton.move — so this exercises the real target
    # state without needing the "mood" signal to actually be >= 70.
    response = client.post(f"/api/core/sessions/{session_id}/actions", json={"action_name": "notice_mood"})

    assert response.status_code == 200
    assert response.json()["state"]["key"] == "mood_reached"
    # Unlike the self-looping variant, this is a genuine final state.
    assert response.json()["state"]["final"] is True
