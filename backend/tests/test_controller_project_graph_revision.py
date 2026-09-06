"""GET .../graph, .../signals, .../env-keys take an optional `session_id`
query param: omitted, they read the current draft; given, they pin to
the exact revision that session's automaton actually ran against.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract

TWO_STATE_YML = (
    "project:\n  id: proj\n"
    "init-action:\n  target: a\n"
    "states:\n"
    "  a:\n"
    "    contextual-prompt: hi\n"
    "    actions:\n"
    "      - name: go\n"
    "        target: b\n"
    "  b:\n"
    "    contextual-prompt: there\n"
)

THREE_STATE_YML = TWO_STATE_YML + "  c:\n    contextual-prompt: new\n"


def _upload(client, yml: str, publish: bool = True):
    client.put("/api/projects/proj/files/index.yml", content=yml.encode(), headers={"Content-Type": "application/x-yaml"}) \
        if client.get("/api/projects/proj/files/index.yml").status_code == 200 else \
        client.post("/api/projects/upload", content=yml.encode(), headers={"Content-Type": "application/x-yaml"})
    if publish:
        client.post("/api/projects/proj/publish", json={})


def _fire_action(client, session_id: int) -> None:
    """Establishes current_state reliably — a session with no real action
    fired yet is wiped the next time the project is edited."""
    response = client.post(f"/api/chat/sessions/{session_id}/action", json={"action_name": "go"})
    assert response.status_code == 200, response.text


def _pinned_live_session(client) -> int:
    _upload(client, TWO_STATE_YML)
    response = client.get("/api/chat/session")
    assert response.status_code == 200, response.text
    session_id = response.json()["id"]
    _fire_action(client, session_id)
    return session_id


def _state_keys(response) -> set[str]:
    return {n["state"]["key"] for n in response.json()["nodes"]}


def test_the_graph_follows_the_draft_unless_pinned_to_a_live_sessions_own_published_revision(client):
    session_id = _pinned_live_session(client)
    _upload(client, THREE_STATE_YML)

    current = client.get("/api/projects/proj/graph")
    pinned = client.get(f"/api/projects/proj/graph?session_id={session_id}")

    assert current.status_code == 200
    assert _state_keys(current) == {"a", "b", "c"}
    assert current.json()["revision"] == 1
    assert _state_keys(pinned) == {"a", "b"}
    assert pinned.json()["revision"] == 0

    assert client.get("/api/projects/proj/graph?session_id=999999").status_code == 404


def test_a_test_session_always_tracks_the_live_draft(client):
    _upload(client, TWO_STATE_YML)
    response = client.post("/api/projects/proj/test-sessions")
    assert response.status_code == 200, response.text
    _fire_action(client, response.json()["id"])
    test_session_id = response.json()["id"]

    _upload(client, THREE_STATE_YML, publish=False)

    response = client.get(f"/api/projects/proj/graph?session_id={test_session_id}")

    assert response.status_code == 200, response.text
    assert _state_keys(response) == {"a", "b", "c"}


@pytest.mark.parametrize(("route", "declaration", "payload_key", "name_of"), [
    ("signals", 'signals:\n  mood:\n    definition: "1"\n', "signals", lambda row: row["signal"]["name"]),
    ("env-keys", "env:\n  greeting:\n    value: \"'hi'\"\n", "env_keys", lambda row: row["env_key"]["name"]),
])
def test_signals_and_env_keys_pin_to_a_sessions_own_revision_the_same_way_the_graph_does(client, route, declaration, payload_key, name_of):
    session_id = _pinned_live_session(client)
    _upload(client, TWO_STATE_YML + declaration)

    pinned = client.get(f"/api/projects/proj/{route}?session_id={session_id}")
    current = client.get(f"/api/projects/proj/{route}")

    assert pinned.json()[payload_key] == []
    assert [name_of(row) for row in current.json()[payload_key]] == [declaration.split(":")[1].split()[0].rstrip(":")]
