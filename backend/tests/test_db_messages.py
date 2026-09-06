from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.contract


def _make_session(db, *, username="user", project_name="proj", start=datetime(2026, 1, 1)):
    db.ensure_project(project_name)
    db.publish_project(project_name)
    return db.create_chat_session(
        username=username,
        project_id=project_name,
        revision=db.get_project_published_revision(project_name),
        datetime_start=start,
        datetime_end=start,
        start_state="a",
        end_state="a",
    )


def _seed_turns(db, session_id, token_costs, start=datetime(2026, 1, 1, 12, 0, 0)):
    """Alternating user/assistant messages, each user message marked as
    answered by the reply that follows it — the shape a real conversation
    always has (see Message.answered_by), and what tells get_turn_history
    which turn a message belongs to."""
    pending: list[int] = []
    for i, tokens in enumerate(token_costs):
        role = "user" if i % 2 == 0 else "assistant"
        message_id = db.save_message(
            role, f"msg{i}", session_id, tokens=tokens, timestamp=start + timedelta(minutes=i),
        )
        if role == "user":
            pending.append(message_id)
        else:
            db.mark_messages_answered(pending, message_id)
            pending = []


def test_a_saved_message_reads_back_in_full_by_id_or_through_its_session_with_reaction_defaulting_to_none(db):
    session_id = _make_session(db)
    message_id = db.save_message("user", "hello", session_id, audio_text="spoken", reaction="supportive")
    plain_id = db.save_message("assistant", "hi", session_id)

    message = db.get_message(message_id)
    assert message["id"] == message_id
    assert message["role"] == "user"
    assert message["content"] == "hello"
    assert message["audio_text"] == "spoken"
    assert message["reaction"] == "supportive"
    assert message["session_id"] == session_id
    assert isinstance(message["timestamp"], str)

    assert db.get_message(plain_id)["reaction"] is None
    assert db.get_message(999999) is None

    rows = db.get_messages(session_id)
    assert [r["reaction"] for r in rows] == ["supportive", None]
    assert all(r["session_id"] == session_id for r in rows)


def test_get_turn_history_returns_every_message_with_no_budget_and_only_the_latest_ones_fitting_a_given_one(db):
    session_id = _make_session(db)
    _seed_turns(db, session_id, [100, 50, 200, 80, 300, 120])

    assert [r["content"] for r in db.get_turn_history(session_id, None, None)] == [
        f"msg{i}" for i in range(6)
    ]
    assert [r["content"] for r in db.get_turn_history(session_id, None, 200)] == ["msg5"]


def test_get_turn_history_combines_since_and_budget_in_one_pass_treating_an_unknown_cost_as_free(db):
    start = datetime(2026, 1, 1, 12, 0, 0)
    session_id = _make_session(db)
    _seed_turns(db, session_id, [100, 50, 200, 80, 300, 120], start=start)

    rows = db.get_turn_history(session_id, start + timedelta(minutes=1), 100000)
    assert [r["content"] for r in rows] == ["msg2", "msg3", "msg4", "msg5"]

    free = _make_session(db, project_name="free-proj")
    asked = db.save_message("user", "no-tokens", free, timestamp=start)
    answered = db.save_message("assistant", "no-tokens-reply", free, timestamp=start + timedelta(minutes=1))
    db.mark_messages_answered([asked], answered)
    assert [r["content"] for r in db.get_turn_history(free, None, 8000)] == ["no-tokens", "no-tokens-reply"]
