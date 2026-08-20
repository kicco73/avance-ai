"""Integration test for SessionSummaryManager's auto-queue hook: a
session discovered closed the moment a new one is about to be created
gets its summary job submitted and eventually readable via the API.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.contract


def test_closing_a_session_queues_its_summary(client, app_db, hello_project):
    session = client.get("/api/chat/session").json()
    client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"})

    # No content yet — nothing has queued a summary for it so far.
    assert client.get(f"/api/chat/sessions/{session['id']}/summary").json() == {"content": None}

    # Backdate this session's own datetime_end far enough to count as
    # closed under ChatSessionManager's default 60-minute open window.
    app_db.touch_chat_session(session["id"], datetime.utcnow() - timedelta(hours=2), session["end_state"])

    # No session is active anymore — this call's own hook discovers the
    # previous one closed and queues its summary before creating a new one.
    new_session = client.get("/api/chat/session").json()
    assert new_session["id"] != session["id"]

    deadline = time.monotonic() + 5.0
    summary = {"content": None}
    while time.monotonic() < deadline and summary["content"] is None:
        summary = client.get(f"/api/chat/sessions/{session['id']}/summary").json()
        if summary["content"] is None:
            time.sleep(0.05)

    assert summary["content"] is not None


def test_a_still_open_session_is_never_queued(client, hello_project):
    session = client.get("/api/chat/session").json()
    client.post(f"/api/chat/sessions/{session['id']}/messages", json={"message": "hi"})

    # Calling get_or_create again immediately (session still well within
    # the open window) must never queue anything for it.
    client.get("/api/chat/session")

    assert client.get(f"/api/chat/sessions/{session['id']}/summary").json() == {"content": None}
