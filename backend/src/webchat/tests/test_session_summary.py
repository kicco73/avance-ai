"""Closing a live session queues SessionReportTask (session_report_task.py),
which renders the session's turns/signals into a prompt, calls
AiService.prompt(), and stores the result on CoreSession.ai_summary.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from conftest import run_pending_tasks, chat_turn, enter_chat, session_of
from webchat.tests.webchat_helpers import end_chat

pytestmark = pytest.mark.contract


def test_closing_a_live_session_produces_a_summary(client, app, app_db, hello_project):
    session_id = session_of(enter_chat(client, hello_project))
    chat_turn(client, session_id, "hi")
    assert app_db.get_chat_session(session_id)["ai_summary"] is None

    end_chat(client, session_id)

    run_pending_tasks(app)

    assert app_db.get_chat_session(session_id)["ai_summary"] == "Fake AI reply."


def test_closing_a_live_session_also_sets_the_title_and_the_apps_ai_summary(client, app, app_db, hello_project):
    app_db.install_project("user", hello_project)
    session_id = session_of(enter_chat(client, hello_project))
    chat_turn(client, session_id, "hi")
    end_chat(client, session_id)

    run_pending_tasks(app)

    assert app_db.get_chat_session(session_id)["title"] == "Fake title."
    apps = app_db.list_projects_for_app_store("user")
    mine = next(a for a in apps if a["id"] == hello_project)
    assert mine["ai_summary"] == "Fake AI reply."


def test_a_still_open_session_has_no_summary(client, app, app_db, hello_project):
    session_id = session_of(enter_chat(client, hello_project))
    chat_turn(client, session_id, "hi")
    run_pending_tasks(app)

    assert app_db.get_chat_session(session_id)["ai_summary"] is None


def test_a_session_merely_expired_by_the_open_window_is_never_queued(client, app, app_db, hello_project):
    """Expiring past the open window is not the same as being closed —
    only an explicit close (SessionManager.close_session) schedules
    a report, so a session nobody ever closed must never get one even
    once it reads as closed via is_open()."""
    session_id = session_of(enter_chat(client, hello_project))
    chat_turn(client, session_id, "hi")
    end_state = app_db.get_chat_session(session_id)["end_state"]
    app_db.touch_chat_session(session_id, datetime.utcnow() - timedelta(hours=2), end_state)

    assert session_of(enter_chat(client, hello_project)) != session_id

    run_pending_tasks(app)

    assert app_db.get_chat_session(session_id)["ai_summary"] is None
