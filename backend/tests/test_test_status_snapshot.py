from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.contract


def test_get_test_status_reflects_a_completed_job(client, hello_project):
    """Replaces the old SSE probe: test-run progress is now delivered
    live over /ws/notifications (see queue_progress_broadcaster.py), and
    this endpoint only serves the same broadcaster's own last-known-state
    snapshot — a plain GET, pollable, no live connection needed."""
    session = client.get("/api/chat/session").json()
    session_id = session["id"]
    client.post(f"/api/chat/sessions/{session_id}/messages", json={"message": "hi"})
    client.put(f"/api/chat/sessions/{session_id}/labeled", json={"labeled": True})

    target_key = f"batch:session:{session_id}"
    post_resp = client.post(f"/api/projects/{hello_project}/tests", json={"session_id": session_id, "strategy": "batch"})
    assert post_resp.status_code == 200, post_resp.text

    deadline = time.monotonic() + 10.0
    matching = None
    while time.monotonic() < deadline:
        events = client.get(f"/api/projects/{hello_project}/test-status").json()["events"]
        matching = next((e for e in events if e.get("key") == target_key), None)
        if matching is not None and matching.get("job_status") in ("completed", "failed"):
            break
        time.sleep(0.1)

    assert matching is not None and matching.get("job_status") in ("completed", "failed"), matching
