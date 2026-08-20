"""ProjectService._finalize_project_update's own reconciliation: editing a
project file no longer wipes the live conversation unconditionally — only
when the state it was actually in doesn't survive the edit.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.regression

TWO_STATE_YML = (
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


def _upload_and_reach_b(client):
    resp = client.put("/api/projects/proj", content=TWO_STATE_YML.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    resp = client.post("/api/projects/proj/publish", json={})
    assert resp.status_code == 200, resp.text

    session = client.get("/api/chat/session").json()
    action_resp = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "go"})
    assert action_resp.status_code == 200, action_resp.text
    assert action_resp.json()["state"]["key"] == "b"
    return session


def test_editing_a_file_without_touching_the_current_state_keeps_the_conversation(client):
    session = _upload_and_reach_b(client)

    # Adds an unrelated state "c" — "b" (the one the conversation is
    # actually in) is untouched.
    yml_v2 = TWO_STATE_YML + "  c:\n    contextual-prompt: extra\n"
    resp = client.put("/api/projects/proj/files/index.yml", content=yml_v2.encode())
    assert resp.status_code == 200, resp.text

    sessions = client.get("/api/projects/proj/sessions").json()
    assert [s["id"] for s in sessions] == [session["id"]]
    assert client.get("/api/state").json()["key"] == "b"


def test_editing_a_file_that_removes_the_current_state_resets_the_conversation(client):
    _upload_and_reach_b(client)

    # "b" (the state the conversation is currently in) no longer exists.
    yml_v2 = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"
    resp = client.put("/api/projects/proj/files/index.yml", content=yml_v2.encode())
    assert resp.status_code == 200, resp.text

    assert client.get("/api/projects/proj/sessions").json() == []
    assert client.get("/api/state").json()["key"] == "a"


def test_editing_a_file_that_renames_the_current_state_resets_the_conversation(client):
    """A rename is, from the persisted Tracking row's point of view,
    exactly the same as a removal — the old key simply isn't a state
    anymore, whether or not something equivalent exists under a new one."""
    _upload_and_reach_b(client)

    yml_v2 = (
        "init-action:\n  target: a\n"
        "states:\n"
        "  a:\n"
        "    contextual-prompt: hi\n"
        "    actions:\n"
        "      - name: go\n"
        "        ui-label: Go\n"
        "        ui-button: Go\n"
        "        target: b-renamed\n"
        "  b-renamed:\n"
        "    contextual-prompt: there\n"
    )
    resp = client.put("/api/projects/proj/files/index.yml", content=yml_v2.encode())
    assert resp.status_code == 200, resp.text

    assert client.get("/api/projects/proj/sessions").json() == []
    assert client.get("/api/state").json()["key"] == "a"


def test_editing_an_unrelated_project_does_not_touch_the_active_ones_conversation(client):
    """_finalize_project_update only reconciles when `project_name` is the
    *active* one — editing some other project's file must never wipe the
    conversation that's actually running right now."""
    session = _upload_and_reach_b(client)

    other_yml = "init-action:\n  target: x\nstates:\n  x:\n    contextual-prompt: hi\n"
    resp = client.put("/api/projects/other", content=other_yml.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    # Uploading "other" activates it — reactivate "proj" to restore the
    # scenario this test is actually about (some *other* project's file
    # being edited while "proj" stays the active one).
    client.put("/api/projects/proj/activate")

    resp = client.put("/api/projects/other/files/index.yml", content=b"init-action:\n  target: y\nstates:\n  y:\n    contextual-prompt: hi\n")
    assert resp.status_code == 200, resp.text

    sessions = client.get("/api/projects/proj/sessions").json()
    assert [s["id"] for s in sessions] == [session["id"]]
    assert client.get("/api/state").json()["key"] == "b"
