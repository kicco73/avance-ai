"""Closing a live session queues SessionReportTask (session_report_task.py),
which renders the session's turns/signals into a prompt, calls
AiService.prompt(), and stores the result on ChatSession.ai_summary.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from conftest import run_on_enter_tasks, chat_turn

pytestmark = pytest.mark.contract


def test_closing_a_live_session_produces_a_summary(client, app, app_db, hello_project):
    session = client.get("/api/chat/session").json()
    chat_turn(client, session['id'], "hi")
    assert app_db.get_chat_session(session["id"])["ai_summary"] is None

    resp = client.post(f"/api/chat/sessions/{session['id']}/close")
    assert resp.status_code == 200, resp.text

    run_on_enter_tasks(app)

    assert app_db.get_chat_session(session["id"])["ai_summary"] == "Fake AI reply."


def test_closing_a_live_session_also_sets_the_title_and_the_apps_ai_summary(client, app, app_db, hello_project):
    app_db.install_project("user", hello_project)
    session = client.get("/api/chat/session").json()
    chat_turn(client, session['id'], "hi")
    resp = client.post(f"/api/chat/sessions/{session['id']}/close")
    assert resp.status_code == 200, resp.text

    run_on_enter_tasks(app)

    assert app_db.get_chat_session(session["id"])["title"] == "Fake title."
    apps = app_db.list_projects_for_app_store("user")
    mine = next(a for a in apps if a["id"] == hello_project)
    assert mine["ai_summary"] == "Fake AI reply."


def test_a_still_open_session_has_no_summary(client, app, app_db, hello_project):
    session = client.get("/api/chat/session").json()
    chat_turn(client, session['id'], "hi")
    run_on_enter_tasks(app)

    assert app_db.get_chat_session(session["id"])["ai_summary"] is None


def test_a_session_merely_expired_by_the_open_window_is_never_queued(client, app, app_db, hello_project):
    """Expiring past the open window is not the same as being closed —
    only an explicit close (ChatSessionManager.close_session) schedules
    a report, so a session nobody ever closed must never get one even
    once it reads as closed via is_open()."""
    session = client.get("/api/chat/session").json()
    chat_turn(client, session['id'], "hi")
    app_db.touch_chat_session(session["id"], datetime.utcnow() - timedelta(hours=2), session["end_state"])

    new_session = client.get("/api/chat/session").json()
    assert new_session["id"] != session["id"]

    run_on_enter_tasks(app)

    assert app_db.get_chat_session(session["id"])["ai_summary"] is None
