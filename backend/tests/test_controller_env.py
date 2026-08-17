"""GET/PUT/DELETE /api/chat/env — see ChatService.get_env/set_env_value/
delete_env_key/clear_env (chat.env.Env) — {"stored": ..., "action_set":
..., "computed": ...} split so the "Edit project" view's Inspector Env
tab knows which section each value belongs in ("AI"/"ACTION"/"COMPUTED")
and which are actually editable/deletable. `action_set` (values an
action's own YAML `env:` field set — see automaton_builder.py's
_build_action) has no dedicated PUT/DELETE endpoint of its own — it's
never a human's direct edit, only ever a side effect of an action
firing (see test_chat_service_manual_action_env.py/
test_auto_tracker_action_env.py for that path). `message_id`, when
given, restricts to values as they stood at or before that exact
message — same point-in-time convention as GET /api/chat/metrics.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import ENV_COMPUTED_KEYS

pytestmark = pytest.mark.contract


def test_env_endpoint_reports_every_computed_key_even_with_nothing_stored(client, hello_project):
    response = client.get("/api/chat/env")

    assert response.status_code == 200
    body = response.json()
    assert body["stored"] == {}
    assert body["action_set"] == {}
    for key in ENV_COMPUTED_KEYS:
        assert key in body["computed"]


def test_env_endpoint_reflects_sessions_created_so_far(client, hello_project):
    client.get("/api/chat/session")

    body = client.get("/api/chat/env").json()

    assert body["computed"]["number_of_user_sessions"] >= 1


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


def test_put_env_value_rejects_a_computed_key(client, hello_project):
    response = client.put("/api/chat/env/number_of_user_sessions", json={"value": "99"})

    assert response.status_code == 400


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


def test_delete_env_value_rejects_a_computed_key(client, hello_project):
    response = client.delete("/api/chat/env/today")

    assert response.status_code == 400


def test_env_with_a_message_id_restricts_to_a_point_in_time(client, hello_project):
    session = client.get("/api/chat/session").json()
    resp = client.post("/api/chat/messages", json={"message": "hello", "session_id": session["id"]})
    message_id = next(m["id"] for m in resp.json()["reply"] if m.get("id") is not None)

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


def test_clear_env_leaves_computed_values_untouched(client, hello_project):
    client.get("/api/chat/session")
    client.put("/api/chat/env/mood", json={"value": "happy"})

    body = client.delete("/api/chat/env").json()

    assert body["computed"]["number_of_user_sessions"] >= 1


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


def test_clear_action_env_leaves_stored_and_computed_untouched(client, hello_project, app_db):
    client.get("/api/chat/session")
    client.put("/api/chat/env/favorite_color", json={"value": "blue"})
    app_db.set_action_env("hello", {"a": 1}, "user")

    body = client.delete("/api/chat/action-env").json()

    assert body["stored"] == {"favorite_color": "blue"}
    assert body["computed"]["number_of_user_sessions"] >= 1


def test_clear_action_env_bootstraps_a_session_if_none_exists_yet(client, hello_project):
    response = client.delete("/api/chat/action-env")
    assert response.status_code == 200
    assert response.json()["action_set"] == {}
