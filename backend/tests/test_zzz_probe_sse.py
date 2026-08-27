from __future__ import annotations

import json
import threading
import time

import pytest

from session import Session

pytestmark = pytest.mark.contract


def test_probe_session_play_button_progress(client, hello_project):
    session = client.get("/api/chat/session").json()
    session_id = session["id"]
    client.post(f"/api/chat/sessions/{session_id}/messages", json={"message": "hi"})
    client.put(f"/api/chat/sessions/{session_id}/labeled", json={"labeled": True})

    messages = []
    stop = threading.Event()

    def listen():
        Session().user = "user"
        Session().role = "supervisor"
        with client.stream("GET", "/api/projects/hello/test-events") as resp:
            for line in resp.iter_lines():
                if stop.is_set():
                    break
                if line.startswith("data: "):
                    msg = json.loads(line[len("data: "):])
                    print("SSE MESSAGE:", msg)
                    messages.append(msg)
                    if msg.get("status") in ("completed", "failed") and msg.get("key", "").startswith(f"batch:session:{session_id}"):
                        break

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    time.sleep(0.3)

    resp = client.post("/api/projects/hello/tests", json={"session_id": session_id, "strategy": "batch"})
    print("POST result:", resp.json())

    t.join(timeout=10)
    stop.set()

    print("ALL MESSAGES:", messages)
    assert any(m.get("key") == f"batch:session:{session_id}" for m in messages), messages
