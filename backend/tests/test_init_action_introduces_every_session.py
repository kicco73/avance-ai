"""The init-action runs whenever the automaton starts or restarts.

Not "whenever a session is created": a live session under `resume`
continues where the previous one left and starts nothing. But a project
running for the first time, and every session of a type that restarts —
test and preview always, live under `restart` — is the automaton
starting, and a state is only ever entered by an action, the initial one
included.

Which request created the session does not enter into it. `session.create`
says so outright; `session.enter` creates one too whenever it finds none
open, and that is the path a browser takes when it shows a chat — the
Test panel included.

What this defends is what a project reads out of its own init-action
afterwards. An automaton that was never started runs on the declared
defaults instead, and the first action reading one of those keys fails —
`env.questions[0]` on an empty list.
"""
from __future__ import annotations

import pytest

from conftest import chat_action, create_chat, enter_chat, session_of

pytestmark = pytest.mark.regression

PROJECT_ID = "introduced"

INDEX_YML = """
project:
  id: introduced
  new-session-strategy: restart
init-action:
  target: Hello
  on-exit: "env.questions = source.q.column('question')"
env:
  questions:
    type: choice
    value: ''
  question:
    type: string
    value: ''
sources:
  q:
    url: avance:sources/q.csv
states:
  Hello:
    ui-label: Hello
    contextual-prompt: hi
    chat-enabled: false
    actions:
      - name: go
        ui-label: Go
        target: Bye
        on-exit: "env.question = env.questions[0]"
  Bye:
    ui-label: Bye
    contextual-prompt: bye
    chat-enabled: false
"""

QUESTIONS_CSV = b"id;question\n1;first?\n2;second?\n"


@pytest.fixture
def project(client, app_db):
    app_db.ensure_project(PROJECT_ID)
    app_db.save_project_files(
        PROJECT_ID,
        {"index.yml": INDEX_YML.encode("utf-8"), "sources/q.csv": QUESTIONS_CSV},
        {"index.yml": "text/yaml", "sources/q.csv": "text/csv"},
    )
    app_db.publish_project(PROJECT_ID)
    app_db.set_active_project_id(PROJECT_ID, "user")
    return PROJECT_ID


def _introductions(app_db, session_id: int) -> list[str]:
    return [row["origin"] for row in app_db.get_signals(session_id) if row["old_state"] == ""]


def test_entering_a_chat_reads_the_init_actions_own_env(client, app_db, project):
    session_id = session_of(enter_chat(client, project))

    chat_action(client, session_id, "go")

    assert _introductions(app_db, session_id) == ["init-action"]
    assert app_db.get_action_env(project, "user")["question"] == "first?"


def test_the_next_session_entered_restarts_the_automaton(client, app_db, project):
    first = session_of(enter_chat(client, project))
    chat_action(client, first, "go")

    second = session_of(enter_chat(client, project))
    assert second != first
    chat_action(client, second, "go")

    assert _introductions(app_db, second) == ["init-action"]
    assert app_db.get_action_env(project, "user")["question"] == "first?"


def test_a_session_created_outright_is_introduced_once(client, app_db, project):
    first = session_of(enter_chat(client, project))
    chat_action(client, first, "go")

    second = session_of(create_chat(client, project))
    chat_action(client, second, "go")

    assert _introductions(app_db, second) == ["init-action"]


def test_re_entering_an_open_conversation_introduces_nothing_again(client, app_db, project):
    session_id = session_of(enter_chat(client, project))

    assert session_of(enter_chat(client, project)) == session_id

    assert _introductions(app_db, session_id) == ["init-action"]


def test_a_test_session_entered_from_the_panel_is_introduced(client, app_db, project):
    first = session_of(enter_chat(client, project, "test"))
    chat_action(client, first, "go")

    second = session_of(enter_chat(client, project, "test"))
    assert second != first
    chat_action(client, second, "go")

    assert _introductions(app_db, second) == ["init-action"]
