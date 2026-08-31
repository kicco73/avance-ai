from __future__ import annotations

import json

import httpx
import pytest

pytestmark = pytest.mark.contract


def test_probe_session_play_button_progress(client, hello_project, live_server):
    """Needs `live_server` (a real socket), not `client`: this endpoint
    only ends on client disconnect, which Starlette's in-process
    TestClient can never observe (see live_server's own docstring in
    conftest.py)."""
    session = client.get("/api/chat/session").json()
    session_id = session["id"]
    client.post(f"/api/chat/sessions/{session_id}/messages", json={"message": "hi"})
    client.put(f"/api/chat/sessions/{session_id}/labeled", json={"labeled": True})

    target_key = f"batch:session:{session_id}"
    messages = []
    with httpx.Client(base_url=live_server, timeout=10.0) as real_client:
        with real_client.stream("GET", "/api/projects/hello/test-events") as resp:
            # Entering the stream already waited for response headers, so
            # the broadcaster connection this endpoint registers on entry
            # is live before the job below can push anything — no
            # arbitrary sleep needed to avoid a race with it.
            resp_iter = resp.iter_lines()
            post_resp = client.post("/api/projects/hello/tests", json={"session_id": session_id, "strategy": "batch"})
            assert post_resp.status_code == 200, post_resp.text

            for line in resp_iter:
                if not line.startswith("data: "):
                    continue
                msg = json.loads(line[len("data: "):])
                messages.append(msg)
                if msg.get("key") == target_key and msg.get("job_status") in ("completed", "failed"):
                    break

    assert any(m.get("key") == target_key for m in messages), messages
