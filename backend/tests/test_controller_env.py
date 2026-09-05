"""GET/PUT/DELETE /api/chat/sessions/{session_id}/env — returns
{"stored": ..., "action_set": ...}. `action_set` (values an action's
`env:` field set) has no PUT/DELETE endpoint, only ever changing as a
side effect of an action firing. `message_id`, when given, restricts to
values as of that message.
"""
from __future__ import annotations

import pytest

from conftest import parse_chat_turn_sse

pytestmark = pytest.mark.contract


def _session_id(client) -> int:
    return client.get("/api/chat/session").json()["id"]


def test_env_endpoint_reports_stored_and_action_set_only(client, hello_project):
    session_id = _session_id(client)

    response = client.get(f"/api/chat/sessions/{session_id}/env")

    assert response.status_code == 200
    body = response.json()
    assert body == {"stored": {}, "action_set": {}}


def test_put_env_value_stores_it(client, hello_project):
    session_id = _session_id(client)

    response = client.put(f"/api/chat/sessions/{session_id}/env/favorite_color", json={"value": "blue"})

    assert response.status_code == 200
    assert response.json()["stored"] == {"favorite_color": "blue"}
    assert client.get(f"/api/chat/sessions/{session_id}/env").json()["stored"] == {"favorite_color": "blue"}


def test_put_env_value_overwrites_an_existing_key(client, hello_project):
    session_id = _session_id(client)
    client.put(f"/api/chat/sessions/{session_id}/env/favorite_color", json={"value": "blue"})

    response = client.put(f"/api/chat/sessions/{session_id}/env/favorite_color", json={"value": "green"})

    assert response.json()["stored"] == {"favorite_color": "green"}


def test_delete_env_value_removes_the_key(client, hello_project):
    session_id = _session_id(client)
    client.put(f"/api/chat/sessions/{session_id}/env/favorite_color", json={"value": "blue"})
    client.put(f"/api/chat/sessions/{session_id}/env/mood", json={"value": "happy"})

    response = client.delete(f"/api/chat/sessions/{session_id}/env/favorite_color")

    assert response.status_code == 200
    assert response.json()["stored"] == {"mood": "happy"}
    assert client.get(f"/api/chat/sessions/{session_id}/env").json()["stored"] == {"mood": "happy"}


def test_delete_env_value_for_an_unknown_key_is_a_noop(client, hello_project):
    session_id = _session_id(client)
    client.put(f"/api/chat/sessions/{session_id}/env/mood", json={"value": "happy"})

    response = client.delete(f"/api/chat/sessions/{session_id}/env/does-not-exist")

    assert response.status_code == 200
    assert response.json()["stored"] == {"mood": "happy"}


def test_env_with_a_message_id_restricts_to_a_point_in_time(client, hello_project):
    session_id = _session_id(client)
    resp = client.post(f"/api/chat/sessions/{session_id}/messages", json={"message": "hello"})
    message_id = parse_chat_turn_sse(resp)["assistant_message_id"]

    # A value set *after* that message must not show up in its own
    # point-in-time snapshot.
    client.put(f"/api/chat/sessions/{session_id}/env/favorite_color", json={"value": "blue"})

    live = client.get(f"/api/chat/sessions/{session_id}/env").json()
    as_of_message = client.get(f"/api/chat/sessions/{session_id}/env?message_id={message_id}").json()

    assert live["stored"] == {"favorite_color": "blue"}
    assert as_of_message["stored"] == {}


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
    assert response.json()["stored"] == {}
    assert client.get(f"/api/chat/sessions/{session_id}/env").json()["stored"] == {}
