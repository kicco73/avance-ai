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

from system.web_session import WebSession

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
    type: list
  question:
    type: string
sources:
  q:
    url: avance:sources/q.csv
states:
  Hello:
    ui-label: Hello
    input-processor: ai
    contextual-prompt: hi
    chat-enabled: false
    actions:
      - name: go
        ui-label: Go
        target: Bye
        on-exit: "env.question = env.questions[0]"
  Bye:
    ui-label: Bye
    input-processor: ai
    contextual-prompt: bye
    chat-enabled: false
"""

RESUMING_ID = "resuming"

RESUMING_YML = INDEX_YML.replace("id: introduced", "id: resuming").replace(
    "new-session-strategy: restart", "new-session-strategy: resume"
)

QUESTIONS_CSV = b"id;question\n1;first?\n2;second?\n"


def _publish(app_db, project_id: str, index_yml: str) -> str:
    app_db.ensure_project(project_id)
    app_db.save_project_files(
        project_id,
        {"index.yml": index_yml.encode("utf-8"), "sources/q.csv": QUESTIONS_CSV},
        {"index.yml": "text/yaml", "sources/q.csv": "text/csv"},
    )
    app_db.publish_project(project_id)
    app_db.set_active_project_id(project_id, "user")
    return project_id


@pytest.fixture
def project(client, app_db):
    return _publish(app_db, PROJECT_ID, INDEX_YML)


@pytest.fixture
def resuming(client, app_db):
    return _publish(app_db, RESUMING_ID, RESUMING_YML)


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


def test_a_preview_session_is_introduced(client, app_db, project):
    session_id = session_of(enter_chat(client, project, "preview"))

    chat_action(client, session_id, "go")

    assert _introductions(app_db, session_id) == ["init-action"]


def test_the_first_offer_reflects_what_the_init_action_wrote(client, app_db, project):
    frames = enter_chat(client, project)

    buttons = next(frame for frame in frames if frame["type"] == "state.buttons")
    assert [button["name"] for button in buttons["actions"]] == ["go"]
    assert app_db.get_action_env(project, "user")["questions"] == ["first?", "second?"]


def test_restarting_leaves_nothing_of_the_previous_session_behind(client, app_db, project):
    first = session_of(enter_chat(client, project))
    chat_action(client, first, "go")
    assert app_db.get_action_env(project, "user")["question"] == "first?"

    second = session_of(create_chat(client, project))

    assert second != first
    assert app_db.get_action_env(project, "user")["question"] == ""


def test_the_first_live_session_under_resume_starts_the_automaton(client, app_db, resuming):
    session_id = session_of(enter_chat(client, resuming))

    chat_action(client, session_id, "go")

    assert _introductions(app_db, session_id) == ["init-action"]
    assert app_db.get_action_env(resuming, "user")["question"] == "first?"


def test_a_later_session_under_resume_starts_nothing(client, app_db, resuming):
    first = session_of(enter_chat(client, resuming))
    chat_action(client, first, "go")

    second = session_of(create_chat(client, resuming))

    assert second != first
    assert _introductions(app_db, second) == []


def test_under_resume_every_user_gets_their_own_start(client, app_db, resuming):
    mine = session_of(enter_chat(client, resuming))
    chat_action(client, mine, "go")

    WebSession().user = "other"
    theirs = session_of(enter_chat(client, resuming))
    chat_action(client, theirs, "go")

    assert _introductions(app_db, theirs) == ["init-action"]
    assert app_db.get_action_env(resuming, "other")["question"] == "first?"


def test_a_test_session_that_already_ran_does_not_stand_in_for_the_live_one(client, app_db, resuming):
    tried = session_of(enter_chat(client, resuming, "test"))
    chat_action(client, tried, "go")

    live = session_of(enter_chat(client, resuming))

    assert _introductions(app_db, live) == ["init-action"]


def test_resetting_the_test_sessions_leaves_the_next_one_to_introduce_itself(client, app_db, project):
    """Reset wipes the test sessions' own tracking and env — it does not
    introduce anything itself, outside any transition. The next test
    session does, at its birth, exactly once."""
    tried = session_of(enter_chat(client, project, "test"))
    chat_action(client, tried, "go")

    assert client.post(f"/api/skills/platform/projects/{project}/test-sessions/reset").status_code == 200
    fresh = session_of(enter_chat(client, project, "test"))

    assert _introductions(app_db, fresh) == ["init-action"]


async def test_a_session_opened_to_record_an_unsolicited_reply_is_introduced(client, app_db, project):
    await client.app.state.turn_service.record_unsolicited_reply("user", project, "Anything new?")

    session_id = session_of(enter_chat(client, project))

    assert _introductions(app_db, session_id) == ["init-action"]
    assert app_db.get_action_env(project, "user")["questions"] == ["first?", "second?"]
