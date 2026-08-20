"""GET /api/chat/messages for an imported session (see ChatSession.source)
used to 409 ("Session is not active") whenever the *live* conversation's
current state happened to be final or chat: false — ChatService.
open_if_needed routed through _should_generate_opening_message, which
gates on message timestamps an imported session's own messages never have
(see tracking.session_import's own save_message(..., timestamp=None)):
has_messages_since's `Message.timestamp > since` silently excludes every
NULL-timestamp row, so it wrongly decided a new opening message needed
generating for a session that can never accept one. open_if_needed now
returns immediately for any imported session, before any of that logic
runs at all.
"""
from __future__ import annotations

import io
import zipfile

import pytest

pytestmark = pytest.mark.contract

# A single, final, no-chat state — no outgoing actions (implicitly final,
# see automaton_builder.py's own `final=len(actions) == 0`) and chat:
# false, so the live conversation's own current state trips *both*
# branches of _should_generate_opening_message's chat_blocked check.
INDEX_YML = """
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    chat: false
    actions: []
"""


def _zip_of(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def test_reading_an_imported_sessions_messages_survives_a_final_live_state(client):
    response = client.put(
        "/api/projects/proj", content=_zip_of({"index.yml": INDEX_YML}), headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text
    assert client.put("/api/projects/proj/activate").status_code == 200
    assert client.post("/api/projects/proj/publish", json={}).status_code == 200

    # Bootstraps the live conversation into its own (final, no-chat)
    # state, same as real usage always has happened by the time anyone
    # gets to importing anything in the Benchmark view.
    native = client.post("/api/chat/sessions")
    assert native.status_code == 200, native.text
    assert client.get(f"/api/chat/sessions/{native.json()['id']}/messages").status_code == 200

    imported = client.post(
        "/api/projects/proj/sessions/import", files={"file": ("t.txt", "user: hi\nassistant: hello\n", "text/plain")}
    )
    assert imported.status_code == 200, imported.text
    session_id = imported.json()["session_id"]

    resp = client.get(f"/api/chat/sessions/{session_id}/messages")
    assert resp.status_code == 200, resp.text
    messages = resp.json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
