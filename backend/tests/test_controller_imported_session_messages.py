"""GET /api/chat/messages for an imported session must not 409, even when
the live conversation's current state is final/chat:false —
ChatService.open_if_needed returns immediately for imported sessions.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from conftest import parse_sse_result

pytestmark = pytest.mark.contract

# A single, final, no-chat state (no outgoing actions).
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

    # Bootstraps the live conversation into its final, no-chat state.
    native = client.post("/api/chat/sessions")
    assert native.status_code == 200, native.text
    assert client.get(f"/api/chat/sessions/{native.json()['id']}/messages").status_code == 200

    imported = client.post(
        "/api/projects/proj/sessions/import", files=[("files", ("t.txt", "user: hi\nassistant: hello\n", "text/plain"))]
    )
    assert imported.status_code == 200, imported.text
    session_id = parse_sse_result(imported)["last_session_id"]

    resp = client.get(f"/api/chat/sessions/{session_id}/messages")
    assert resp.status_code == 200, resp.text
    messages = resp.json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
