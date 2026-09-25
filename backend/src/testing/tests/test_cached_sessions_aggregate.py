from __future__ import annotations

import time

import pytest

from conftest import chat_turn, enter_chat, parse_sse_result, session_of

pytestmark = pytest.mark.contract


def _labeled_session(client, project_id):
    session_id = session_of(enter_chat(client, project_id))
    chat_turn(client, session_id, "hi")
    client.put(f"/api/skills/platform/sessions/{session_id}/labeled", json={"labeled": True})
    return session_id


def _signal_result(client, project_id, signal_name, timeout=5.0):
    params = {"kind": "signal", "strategy": "turn_by_turn", "target": signal_name}
    deadline = time.monotonic() + timeout
    response = client.get(f"/api/skills/testing/projects/{project_id}/aggregations/result", params=params)
    while response.status_code != 200 and time.monotonic() < deadline:
        time.sleep(0.05)
        response = client.get(f"/api/skills/testing/projects/{project_id}/aggregations/result", params=params)
    return response


def test_a_signal_test_completes_when_the_sessions_aggregate_it_depends_on_is_already_cached(client, hello_project):
    _labeled_session(client, hello_project)
    client.post(f"/api/skills/testing/projects/{hello_project}/runs/sessions", json={"strategy": "turn_by_turn"})
    params = {"kind": "sessions", "strategy": "turn_by_turn"}
    deadline = time.monotonic() + 5.0
    while (
        client.get(f"/api/skills/testing/projects/{hello_project}/aggregations/result", params=params).status_code != 200
        and time.monotonic() < deadline
    ):
        time.sleep(0.05)
    response = client.post(
        f"/api/skills/platform/projects/{hello_project}/sessions/import",
        files=[("files", ("t.txt", "user: hi\nassistant: yo\n", "text/plain"))],
    )
    other = parse_sse_result(response)["last_session_id"]
    client.put(f"/api/skills/platform/sessions/{other}/labeled", json={"labeled": True})

    client.post(f"/api/skills/testing/projects/{hello_project}/runs/signals/foo", json={"strategy": "turn_by_turn"})

    assert _signal_result(client, hello_project, "foo").status_code == 200
