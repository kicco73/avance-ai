"""GET/PUT/DELETE /api/chat/env — returns {"stored": ..., "action_set": ...}.
`action_set` (values an action's `env:` field set) has no PUT/DELETE
endpoint, only ever changing as a side effect of an action firing.
`message_id`, when given, restricts to values as of that message.
"""
from __future__ import annotations

import pytest

from conftest import parse_chat_turn_sse

pytestmark = pytest.mark.contract


def test_env_endpoint_reports_stored_and_action_set_only(client, hello_project):
    response = client.get("/api/chat/env")

    assert response.status_code == 200
    body = response.json()
    assert body == {"stored": {}, "action_set": {}}


def test_put_env_value_stores_it(client, hello_project):
    client.get("/api/chat/session")

    response = client.put("/api/chat/env/favorite_color", json={"value": "blue"})

    assert response.status_code == 200
    assert response.json()["stored"] == {"favorite_color": "blue"}
    assert client.get("/api/chat/env").json()["stored"] == {"favorite_color": "blue"}


def test_put_env_value_overwrites_an_existing_key(client, hello_project):
    client.put("/api/chat/env/favorite_color", json={"value": "blue"})

    response = client.put("/api/chat/env/favorite_color", json={"value": "green"})

    assert response.json()["stored"] == {"favorite_color": "green"}


def test_delete_env_value_removes_the_key(client, hello_project):
    client.put("/api/chat/env/favorite_color", json={"value": "blue"})
    client.put("/api/chat/env/mood", json={"value": "happy"})

    response = client.delete("/api/chat/env/favorite_color")

    assert response.status_code == 200
    assert response.json()["stored"] == {"mood": "happy"}
    assert client.get("/api/chat/env").json()["stored"] == {"mood": "happy"}


def test_delete_env_value_for_an_unknown_key_is_a_noop(client, hello_project):
    client.put("/api/chat/env/mood", json={"value": "happy"})

    response = client.delete("/api/chat/env/does-not-exist")

    assert response.status_code == 200
    assert response.json()["stored"] == {"mood": "happy"}


def test_env_with_a_message_id_restricts_to_a_point_in_time(client, hello_project):
    session = client.get("/api/chat/session").json()
    resp = client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hello"})
    message_id = parse_chat_turn_sse(resp)["assistant_message_id"]

    # A value set *after* that message must not show up in its own
    # point-in-time snapshot.
    client.put("/api/chat/env/favorite_color", json={"value": "blue"})

    live = client.get("/api/chat/env").json()
    as_of_message = client.get(f"/api/chat/env?message_id={message_id}").json()

    assert live["stored"] == {"favorite_color": "blue"}
    assert as_of_message["stored"] == {}


def test_env_with_an_unknown_message_id_is_404(client, hello_project):
    response = client.get("/api/chat/env?message_id=999999")
    assert response.status_code == 404


def test_clear_env_wipes_every_stored_key(client, hello_project):
    client.put("/api/chat/env/favorite_color", json={"value": "blue"})
    client.put("/api/chat/env/mood", json={"value": "happy"})

    response = client.delete("/api/chat/env")

    assert response.status_code == 200
    assert response.json()["stored"] == {}
    assert client.get("/api/chat/env").json()["stored"] == {}


def test_clear_env_bootstraps_a_session_if_none_exists_yet(client, hello_project):
    response = client.delete("/api/chat/env")
    assert response.status_code == 200
    assert response.json()["stored"] == {}


def test_clear_action_env_wipes_every_action_set_key(client, hello_project, app_db):
    client.get("/api/chat/session")
    app_db.set_action_env("hello", {"a": 1, "b": 2}, "user")
    assert client.get("/api/chat/env").json()["action_set"] == {"a": 1, "b": 2}

    response = client.delete("/api/chat/action-env")

    assert response.status_code == 200
    assert response.json()["action_set"] == {}
    assert client.get("/api/chat/env").json()["action_set"] == {}


def test_clear_action_env_leaves_stored_untouched(client, hello_project, app_db):
    client.get("/api/chat/session")
    client.put("/api/chat/env/favorite_color", json={"value": "blue"})
    app_db.set_action_env("hello", {"a": 1}, "user")

    body = client.delete("/api/chat/action-env").json()

    assert body["stored"] == {"favorite_color": "blue"}


def test_clear_action_env_bootstraps_a_session_if_none_exists_yet(client, hello_project):
    response = client.delete("/api/chat/action-env")
    assert response.status_code == 200
    assert response.json()["action_set"] == {}
