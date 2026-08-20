"""POST /api/action's own "on-enter" — the fired action's own on_enter
(see automaton.Action.on_enter/ChatService.apply_manual_action), sent
over the wire as "on-enter" (kebab-case, matching the YAML field's own
spelling — unlike every other snake_case response key), not anything
read off the destination state. Two different actions landing on the
same state can disagree on whether entering it celebrates.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract

YML = (
    "init-action:\n  target: a\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: go-quiet\n"
    "        target: b\n"
    "      - name: go-loud\n"
    "        target: b\n"
    "        on-enter: celebrate()\n"
    "  b:\n"
    "    contextual-prompt: there\n"
)


def _upload_and_get_session(client):
    resp = client.put("/api/projects/proj", content=YML.encode(), headers={"Content-Type": "application/x-yaml"})
    assert resp.status_code == 200, resp.text
    resp = client.post("/api/projects/proj/publish", json={})
    assert resp.status_code == 200, resp.text
    return client.get("/api/chat/session").json()


def test_manual_action_reports_the_fired_actions_own_on_enter(client):
    session = _upload_and_get_session(client)

    resp = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "go-loud"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"]["key"] == "b"
    assert body["on-enter"] == "celebrate()"


def test_manual_action_without_on_enter_reports_none_even_for_the_same_target_state(client):
    session = _upload_and_get_session(client)

    resp = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "go-quiet"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"]["key"] == "b"
    assert body["on-enter"] is None


def test_state_payload_never_carries_on_enter_itself(client):
    """on-enter used to live on the state payload — confirming it's gone
    from there for good, not just unused."""
    session = _upload_and_get_session(client)

    resp = client.post(f"/api/chat/sessions/{session['id']}/action", json={"action_name": "go-loud"})

    assert "on-enter" not in resp.json()["state"]
    for action in resp.json()["state"]["actions"]:
        assert "on-enter" in action  # present per outgoing action instead


def test_get_state_has_no_on_enter_since_nothing_just_fired(client):
    _upload_and_get_session(client)

    resp = client.get("/api/state")

    assert resp.status_code == 200
    assert "on-enter" not in resp.json()
