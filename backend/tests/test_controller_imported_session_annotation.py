"""An imported session (see ChatSession.source, tracking/session_import.py)
never has any real Tracking rows at all, so its messages used to be
completely unannotatable — every PUT .../expected-state or .../expected-
signals attempt 409'd, since TrackingService._require_annotatable_message
only ever materializes a row for a *live* session's own literal first
message. TrackingService._materialize_imported_session_row is the new
fallback under test here: it lets an expert annotate any message of an
imported session that sits on whichever side (user/assistant)
automaton.autotracking_on_ai_message says a live turn would actually have
evaluated on — same convention a live session already follows, just
without a real row to prove it.
"""
from __future__ import annotations

import io
import zipfile

import pytest

pytestmark = pytest.mark.contract

TRANSCRIPT = "user: hi there\nassistant: hello, world!\n"


def _zip_of(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def _index_yml(*, autotracking_on_ai_message: bool) -> str:
    # A state with no actions is implicitly final (see automaton_builder.
    # py's own `final=len(actions) == 0`) — which would make ChatService.
    # _should_generate_opening_message gate imported-session reads on
    # get_last_transition_timestamp instead of None, spuriously tripping
    # over the NULL timestamps an import's own messages carry (see
    # tracking.session_import's own save_message(..., timestamp=None)) —
    # a real, separate bug, but not this test's own concern, so a no-op
    # self-loop action keeps state "a" non-final to sidestep it here.
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
    # A live session must exist *and already be opened* first (same order
    # real usage follows — the main/embedded chat always bootstraps one
    # well before a user gets to importing anything in the Benchmark
    # view) — otherwise a later GET .../messages for some *other*
    # (imported) session id tries to bootstrap the project's own live
    # conversation (see ChatService.open_if_needed, keyed by project, not
    # by the session_id passed in) from scratch on a call only meant to
    # read that other session's own messages.
    session_resp = client.post("/api/chat/sessions")
    assert session_resp.status_code == 200, session_resp.text
    session_id = session_resp.json()["id"]
    assert client.get("/api/chat/messages", params={"session_id": session_id}).status_code == 200
    return session_id


def _import_and_get_messages(client) -> tuple[int, dict]:
    response = client.post(
        "/api/chat/sessions/import", files={"file": ("transcript.txt", TRANSCRIPT, "text/plain")}
    )
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]
    messages = client.get("/api/chat/messages", params={"session_id": session_id}).json()
    by_role = {m["role"]: m for m in messages}
    assert set(by_role) == {"user", "assistant"}
    return session_id, by_role


def test_user_side_project_allows_annotating_the_user_message(client):
    _setup_project(client, autotracking_on_ai_message=False)
    _, by_role = _import_and_get_messages(client)

    resp = client.put(
        f"/api/chat/messages/{by_role['user']['id']}/expected-state", json={"expected_state": "a"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["expected_state"] == "a"


def test_user_side_project_rejects_annotating_the_assistant_message(client):
    _setup_project(client, autotracking_on_ai_message=False)
    _, by_role = _import_and_get_messages(client)

    resp = client.put(
        f"/api/chat/messages/{by_role['assistant']['id']}/expected-state", json={"expected_state": "a"}
    )
    assert resp.status_code == 409, resp.text


def test_ai_side_project_allows_annotating_the_assistant_message(client):
    _setup_project(client, autotracking_on_ai_message=True)
    _, by_role = _import_and_get_messages(client)

    resp = client.put(
        f"/api/chat/messages/{by_role['assistant']['id']}/expected-state", json={"expected_state": "a"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["expected_state"] == "a"


def test_ai_side_project_rejects_annotating_the_user_message(client):
    _setup_project(client, autotracking_on_ai_message=True)
    _, by_role = _import_and_get_messages(client)

    resp = client.put(
        f"/api/chat/messages/{by_role['user']['id']}/expected-state", json={"expected_state": "a"}
    )
    assert resp.status_code == 409, resp.text


def test_expected_signals_can_also_be_annotated_on_an_imported_session(client):
    _setup_project(client, autotracking_on_ai_message=False)
    _, by_role = _import_and_get_messages(client)

    resp = client.put(
        f"/api/chat/messages/{by_role['user']['id']}/expected-signals", json={"expected_values": {"mood": 80}}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["expected_values"] == '{"mood": 80}'


def test_a_native_sessions_message_is_unaffected_by_the_imported_fallback(client):
    """The fallback only ever applies to source='imported' sessions (see
    TrackingService._materialize_imported_session_row's own session.source
    check) — a real turn's own user message on a native session, which
    auto-tracking never fires anything against ("Hello world"-style
    project, no signals/triggers at all), must still 409 exactly as
    before: only the session's own literal first message gets the
    existing _materialize_session_start_row treatment, never a later one."""
    native_session_id = _setup_project(client, autotracking_on_ai_message=False)
    turn = client.post("/api/chat/messages", json={"session_id": native_session_id, "message": "hi"})
    assert turn.status_code == 200, turn.text
    user_message_id = turn.json()["user_message_id"]

    resp = client.put(
        f"/api/chat/messages/{user_message_id}/expected-state", json={"expected_state": "a"}
    )
    assert resp.status_code == 409, resp.text
