"""Reading an imported session's history must not 409, even when the
live conversation's current state is final/chat:false.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from conftest import enter_chat, parse_sse_result, session_of

pytestmark = pytest.mark.contract

# A single, final, no-chat state (no outgoing actions).
INDEX_YML = """
project:
  id: proj
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
    response = client.post(
        "/api/skills/platform/projects/upload", content=_zip_of({"index.yml": INDEX_YML}), headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text
    assert parse_sse_result(response)["project_id"] == "proj"
    assert client.post("/api/skills/platform/projects/proj/activate").status_code == 200
    assert client.post("/api/skills/platform/projects/proj/publish", json={}).status_code == 200

    # Bootstraps the live conversation into its final, no-chat state.
    native_id = session_of(enter_chat(client, "proj"))
    assert client.get(f"/api/core/sessions/{native_id}/history").status_code == 200

    imported = client.post(
        "/api/skills/platform/projects/proj/sessions/import", files=[("files", ("t.txt", "user: hi\nassistant: hello\n", "text/plain"))]
    )
    assert imported.status_code == 200, imported.text
    session_id = parse_sse_result(imported)["last_session_id"]

    resp = client.get(f"/api/core/sessions/{session_id}/history")
    assert resp.status_code == 200, resp.text
    messages = resp.json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
