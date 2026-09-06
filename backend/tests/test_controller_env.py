"""GET/PUT/DELETE /api/chat/sessions/{session_id}/env — returns
{"memory": ..., "action_set": ..., "ai_access": ...}. `action_set` (the
automaton's env keys, written by an action's `env:` field or the model's
own `update` tool) has no PUT/DELETE endpoint; `ai_access` maps every
declared key to its own ai-access so the Inspector can badge it.
`message_id`, when given, restricts to values as of that message.
"""
from __future__ import annotations

import pytest

from conftest import chat_turn

pytestmark = pytest.mark.contract


def _session_id(client) -> int:
    return client.get("/api/chat/session").json()["id"]


def test_env_endpoint_reports_stored_and_action_set_only(client, hello_project):
    session_id = _session_id(client)

    response = client.get(f"/api/chat/sessions/{session_id}/env")

    assert response.status_code == 200
    body = response.json()
    assert body == {"memory": {}, "action_set": {}, "ai_access": {}}


def test_put_env_value_stores_it(client, hello_project):
    session_id = _session_id(client)

    response = client.put(f"/api/chat/sessions/{session_id}/env/favorite_color", json={"value": "blue"})

    assert response.status_code == 200
    assert response.json()["memory"] == {"favorite_color": "blue"}
    assert client.get(f"/api/chat/sessions/{session_id}/env").json()["memory"] == {"favorite_color": "blue"}


def test_put_env_value_overwrites_an_existing_key(client, hello_project):
    session_id = _session_id(client)
    client.put(f"/api/chat/sessions/{session_id}/env/favorite_color", json={"value": "blue"})

    response = client.put(f"/api/chat/sessions/{session_id}/env/favorite_color", json={"value": "green"})

    assert response.json()["memory"] == {"favorite_color": "green"}


def test_delete_env_value_removes_the_key(client, hello_project):
    session_id = _session_id(client)
    client.put(f"/api/chat/sessions/{session_id}/env/favorite_color", json={"value": "blue"})
    client.put(f"/api/chat/sessions/{session_id}/env/mood", json={"value": "happy"})

    response = client.delete(f"/api/chat/sessions/{session_id}/env/favorite_color")

    assert response.status_code == 200
    assert response.json()["memory"] == {"mood": "happy"}
    assert client.get(f"/api/chat/sessions/{session_id}/env").json()["memory"] == {"mood": "happy"}


def test_delete_env_value_for_an_unknown_key_is_a_noop(client, hello_project):
    session_id = _session_id(client)
    client.put(f"/api/chat/sessions/{session_id}/env/mood", json={"value": "happy"})

    response = client.delete(f"/api/chat/sessions/{session_id}/env/does-not-exist")

    assert response.status_code == 200
    assert response.json()["memory"] == {"mood": "happy"}


def test_env_with_a_message_id_restricts_to_a_point_in_time(client, hello_project):
    session_id = _session_id(client)
    message_id = chat_turn(client, session_id, "hello")["assistant_message_id"]

    # A value set *after* that message must not show up in its own
    # point-in-time snapshot.
    client.put(f"/api/chat/sessions/{session_id}/env/favorite_color", json={"value": "blue"})

    live = client.get(f"/api/chat/sessions/{session_id}/env").json()
    as_of_message = client.get(f"/api/chat/sessions/{session_id}/env?message_id={message_id}").json()

    assert live["memory"] == {"favorite_color": "blue"}
    assert as_of_message["memory"] == {}


def test_env_with_an_unknown_message_id_is_404(client, hello_project):
    session_id = _session_id(client)
    response = client.get(f"/api/chat/sessions/{session_id}/env?message_id=999999")
    assert response.status_code == 404


def test_env_for_an_unknown_session_is_404(client, hello_project):
    response = client.get("/api/chat/sessions/999999/env")
    assert response.status_code == 404


def test_clear_env_wipes_every_stored_key(client, hello_project):
    session_id = _session_id(client)
    client.put(f"/api/chat/sessions/{session_id}/env/favorite_color", json={"value": "blue"})
    client.put(f"/api/chat/sessions/{session_id}/env/mood", json={"value": "happy"})

    response = client.delete(f"/api/chat/sessions/{session_id}/env")

    assert response.status_code == 200
    assert response.json()["memory"] == {}
    assert client.get(f"/api/chat/sessions/{session_id}/env").json()["memory"] == {}
