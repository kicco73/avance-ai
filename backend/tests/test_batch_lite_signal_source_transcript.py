"""BatchLiteSignalSource is BatchSignalSource with only its transcript-
building hooks overridden (_tag_instructions/_transcript_role/
_anchor_message_id) — these tests exercise that transcript directly,
without going through a real AI call, to pin down exactly which messages
and labels reach the model under each autotracking_on_ai_message mode.
BatchSignalSource's own _build_conversation_text is covered here too, as
a regression guard proving the refactor didn't change its output."""
from __future__ import annotations

from datetime import datetime

import pytest

from automaton.automaton import Action, Automaton, Signal, State
from testing.signal_sources import BatchLiteSignalSource, BatchSignalSource

pytestmark = pytest.mark.contract

USERNAME = "user"
PROJECT_ID = "proj"


def _automaton(autotracking_on_ai_message: bool) -> Automaton:
    init_action = Action(name="init", ui_label="init", ui_button="", target="")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action])},
        general_prompt="general",
        signals=[Signal(name="mood", ui_label="Mood", definition="whatever")],
        attachments={},
        general_attachments={},
        autotracking_on_ai_message=autotracking_on_ai_message,
    )


def _session_id(db) -> int:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    return db.create_chat_session(
        username=USERNAME, project_id=PROJECT_ID,
        revision=db.get_project_published_revision(PROJECT_ID),
        datetime_start=datetime(2026, 1, 1), start_state="",
    )


def _seed_conversation(db, session_id: int) -> list[int]:
    """Three user/assistant exchanges — returns the three user message ids,
    the same "turn_ids" TestReplayJob would hand to prepare_batch()."""
    user_ids = []
    for i in range(3):
        user_ids.append(db.save_message("user", f"user says {i}", session_id))
        db.save_message("assistant", f"assistant replies {i}", session_id)
    return user_ids


def _source(cls, db, automaton, session_id: int):
    return cls(ai_service=None, tracking_service=None, db=db, automaton=automaton, session_id=session_id)


def test_batch_signal_source_keeps_both_roles_labeled_at_the_user_message(db):
    automaton = _automaton(autotracking_on_ai_message=False)
    session_id = _session_id(db)
    turn_ids = _seed_conversation(db, session_id)

    text = _source(BatchSignalSource, db, automaton, session_id)._build_conversation_text(turn_ids)

    # The last covered turn's own assistant reply lands after turn_ids[-1]
    # (a higher message id), so it's cut off same as before this refactor —
    # only earlier turns' replies (already <= turn_ids[-1]) are shown.
    assert text == (
        "[Turn 1]\n"
        "User: user says 0\n"
        "Assistant: assistant replies 0\n"
        "[Turn 2]\n"
        "User: user says 1\n"
        "Assistant: assistant replies 1\n"
        "[Turn 3]\n"
        "User: user says 2"
    )


def test_batch_lite_keeps_only_user_messages_when_tracking_before_ai_reply(db):
    automaton = _automaton(autotracking_on_ai_message=False)
    session_id = _session_id(db)
    turn_ids = _seed_conversation(db, session_id)

    source = _source(BatchLiteSignalSource, db, automaton, session_id)
    text = source._build_conversation_text(turn_ids)

    assert text == (
        "[Turn 1]\nUser: user says 0\n"
        "[Turn 2]\nUser: user says 1\n"
        "[Turn 3]\nUser: user says 2"
    )
    assert "user" in source._tag_instructions()
    assert "assistant" in source._tag_instructions()


def test_batch_lite_keeps_only_assistant_messages_when_tracking_after_ai_reply(db):
    automaton = _automaton(autotracking_on_ai_message=True)
    session_id = _session_id(db)
    turn_ids = _seed_conversation(db, session_id)

    source = _source(BatchLiteSignalSource, db, automaton, session_id)
    text = source._build_conversation_text(turn_ids)

    assert text == (
        "[Turn 1]\nAssistant: assistant replies 0\n"
        "[Turn 2]\nAssistant: assistant replies 1\n"
        "[Turn 3]\nAssistant: assistant replies 2"
    )


def test_batch_lite_skips_the_label_for_a_turn_with_no_assistant_reply_yet(db):
    """The trailing user message of an in-progress session has no
    assistant reply to anchor on in "after AI message" mode — that turn's
    label is dropped rather than crashing or mislabeling another line."""
    automaton = _automaton(autotracking_on_ai_message=True)
    session_id = _session_id(db)
    turn_ids = _seed_conversation(db, session_id)
    trailing_user_id = db.save_message("user", "user says 3, unanswered", session_id)
    turn_ids = turn_ids + [trailing_user_id]

    source = _source(BatchLiteSignalSource, db, automaton, session_id)
    text = source._build_conversation_text(turn_ids)

    assert text == (
        "[Turn 1]\nAssistant: assistant replies 0\n"
        "[Turn 2]\nAssistant: assistant replies 1\n"
        "[Turn 3]\nAssistant: assistant replies 2"
    )
