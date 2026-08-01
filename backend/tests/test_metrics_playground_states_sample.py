"""Exercises samples/Metrics Playground (states).zip — the state-based
sibling of Metrics Playground.zip: same signal/metric triggers, but each
one lands on its own dedicated final state instead of looping back to
"engaged". See that file's own test module for the shared assertions;
this one only covers what's specific to landing on a real target state.
"""
from __future__ import annotations

from pathlib import Path

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"


def _upload_and_activate(client, name: str = "metrics-playground-states"):
    content = (SAMPLES_DIR / "Metrics Playground (states).zip").read_bytes()
    response = client.put(f"/api/projects/{name}", content=content, headers={"Content-Type": "application/zip"})
    assert response.status_code == 200, response.text
    response = client.put(f"/api/projects/{name}/activate")
    assert response.status_code == 200, response.text
    return name


def test_the_sample_loads_and_starts_at_lobby(client):
    _upload_and_activate(client)

    session = client.get("/api/chat/session").json()

    assert session["start_state"] == "lobby"


def test_firing_notice_mood_actually_moves_to_its_own_dedicated_state(client):
    _upload_and_activate(client)
    session = client.get("/api/chat/session").json()
    move = client.post("/api/action", json={"action_name": "warm_up", "session_id": session["id"]})
    assert move.json()["state"]["key"] == "engaged"

    # Manual invocation (like clicking the button) never checks the
    # trigger — see Automaton.move — so this exercises the real target
    # state without needing the "mood" signal to actually be >= 70.
    response = client.post("/api/action", json={"action_name": "notice_mood", "session_id": session["id"]})

    assert response.status_code == 200
    assert response.json()["state"]["key"] == "mood_reached"
    # Unlike the self-looping variant, this is a genuine final state.
    assert response.json()["state"]["final"] is True


def test_every_engaged_branch_targets_its_own_distinct_final_state(client):
    _upload_and_activate(client)
    session = client.get("/api/chat/session").json()
    client.post("/api/action", json={"action_name": "warm_up", "session_id": session["id"]})

    response = client.post("/api/triggers/preview", json={"signals": {"mood": 100}})

    targets = {p["action_name"]: p["target"] for p in response.json()}
    assert targets == {
        "notice_mood": "mood_reached",
        "notice_combo": "combo_reached",
        "notice_engagement": "engagement_reached",
        "notice_retention": "retention_reached",
        "notice_consistency": "consistency_reached",
        "notice_stability": "stability_reached",
        "notice_signal_stability": "signal_stability_reached",
    }
    assert len(set(targets.values())) == len(targets)  # every branch's own, distinct state
