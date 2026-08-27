"""An imported session never has any real Tracking rows, so
TrackingService._materialize_imported_session_row lets an expert
annotate any message of it — every line of a reviewed transcript is a
legitimate mark point, regardless of role or autotracking_on_ai_message.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from conftest import parse_chat_turn_sse
from conftest import parse_sse_result

pytestmark = pytest.mark.contract

TRANSCRIPT = "user: hi there\nassistant: hello, world!\n"


def _zip_of(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _index_yml(*, autotracking_on_ai_message: bool) -> str:
    # A state with no actions is implicitly final, which would make
    # imported-session reads gate on transition timestamps and trip over
    # the NULL timestamps an import's messages carry — a no-op self-loop
    # action keeps state "a" non-final to sidestep that here.
    return f"""
init-action:
  target: a
signals:
  mood:
    definition: "How the user feels."
states:
  a:
    contextual-prompt: hi
    actions:
      - name: stay
        target: a
project:
  signal-tracking-on-ai-message: {str(autotracking_on_ai_message).lower()}
"""


def _setup_project(client, *, autotracking_on_ai_message: bool) -> int:
    response = client.put(
        "/api/projects/proj",
        content=_zip_of({"index.yml": _index_yml(autotracking_on_ai_message=autotracking_on_ai_message)}),
        headers={"Content-Type": "application/zip"},
    )
    assert response.status_code == 200, response.text
    assert client.put("/api/projects/proj/activate").status_code == 200
    assert client.post("/api/projects/proj/publish", json={}).status_code == 200
    # A live session must exist and already be opened first — otherwise a
    # later GET .../messages for an imported session id would bootstrap
    # the project's live conversation (keyed by project, not session_id).
    session_resp = client.post("/api/chat/sessions")
    assert session_resp.status_code == 200, session_resp.text
    session_id = session_resp.json()["id"]
    assert client.get(f"/api/chat/sessions/{session_id}/messages").status_code == 200
    return session_id


def _import_and_get_messages(client) -> tuple[int, dict]:
    response = client.post(
        "/api/projects/proj/sessions/import", files=[("files", ("transcript.txt", TRANSCRIPT, "text/plain"))]
    )
    assert response.status_code == 200, response.text
    session_id = parse_sse_result(response)["last_session_id"]
    messages = client.get(f"/api/chat/sessions/{session_id}/messages").json()
    by_role = {m["role"]: m for m in messages}
    assert set(by_role) == {"user", "assistant"}
    return session_id, by_role


def test_allows_annotating_the_user_message_regardless_of_autotracking_side(client):
    _setup_project(client, autotracking_on_ai_message=True)
    _, by_role = _import_and_get_messages(client)

    resp = client.put(
        f"/api/chat/messages/{by_role['user']['id']}/expected-state", json={"expected_state": "a"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["expected_state"] == "a"


def test_allows_annotating_the_assistant_message_regardless_of_autotracking_side(client):
    _setup_project(client, autotracking_on_ai_message=False)
    _, by_role = _import_and_get_messages(client)

    resp = client.put(
        f"/api/chat/messages/{by_role['assistant']['id']}/expected-state", json={"expected_state": "a"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["expected_state"] == "a"


def test_expected_signals_can_also_be_annotated_on_an_imported_session(client):
    _setup_project(client, autotracking_on_ai_message=False)
    _, by_role = _import_and_get_messages(client)

    resp = client.put(
        f"/api/chat/messages/{by_role['user']['id']}/expected-signals", json={"expected_values": {"mood": 80}}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["expected_values"] == '{"mood": 80}'


def test_a_native_sessions_message_is_unaffected_by_the_imported_fallback(client):
    """The fallback only applies to type='imported' sessions — a real
    turn's user message on a live session must still 409; only the
    session's literal first message gets the existing treatment."""
    native_session_id = _setup_project(client, autotracking_on_ai_message=False)
    turn = client.post(f"/api/chat/sessions/{native_session_id}/messages", json={"message": "hi"})
    assert turn.status_code == 200, turn.text
    user_message_id = parse_chat_turn_sse(turn)["user_message_id"]

    resp = client.put(
        f"/api/chat/messages/{user_message_id}/expected-state", json={"expected_state": "a"}
    )
    assert resp.status_code == 409, resp.text


@pytest.mark.regression
def test_annotation_validates_against_the_messages_own_project_not_whatever_is_now_active(client):
    """expected_state/expected_values must validate against the message's
    own project, not whichever project is currently globally active."""
    _setup_project(client, autotracking_on_ai_message=True)
    _, by_role = _import_and_get_messages(client)
    message_id = by_role["user"]["id"]

    # A second, unrelated project becomes active — its own automaton has
    # no state "a" (and no signal "mood") at all, only state "x".
    other_index_yml = """
init-action:
  target: x
states:
  x:
    contextual-prompt: hi
    actions: []
"""
    response = client.put(
        "/api/projects/other",
        content=_zip_of({"index.yml": other_index_yml}),
        headers={"Content-Type": "application/zip"},
    )
    assert response.status_code == 200, response.text
    assert client.put("/api/projects/other/activate").status_code == 200
    assert client.post("/api/projects/other/publish", json={}).status_code == 200

    # Still succeeds — "a" is a real state in the *message's own* project
    # ("proj"), regardless of "other" now being the active one.
    resp = client.put(f"/api/chat/messages/{message_id}/expected-state", json={"expected_state": "a"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["expected_state"] == "a"

    # And a state that's real in "other" but not in "proj" must still be
    # rejected — validation is scoped to "proj", never to "whatever is
    # active", in both directions.
    resp = client.put(f"/api/chat/messages/{message_id}/expected-state", json={"expected_state": "x"})
    assert resp.status_code == 422, resp.text
