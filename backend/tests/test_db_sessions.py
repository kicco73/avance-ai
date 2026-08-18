from __future__ import annotations

from datetime import datetime

import pytest


def _make_session(db, *, username="user", project_name="proj", start, end=None, start_state="start", end_state=None):
    end = end if end is not None else start
    end_state = end_state if end_state is not None else start_state
    db.ensure_project(project_name)
    db.publish_project(project_name)
    return db.create_chat_session(
        username=username,
        project_name=project_name,
        datetime_start=start,
        datetime_end=end,
        start_state=start_state,
        end_state=end_state,
    )


@pytest.mark.contract
def test_create_and_get_chat_session(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))

    session = db.get_chat_session(session_id)

    assert session["username"] == "user"
    assert session["project_name"] == "proj"
    assert session["start_state"] == "start"
    assert session["end_state"] == "start"


@pytest.mark.contract
def test_get_chat_session_returns_none_for_unknown_id(db):
    assert db.get_chat_session(999999) is None


@pytest.mark.regression
def test_create_chat_session_rejects_an_unpublished_project_by_default(db):
    db.ensure_project("draft-only")
    with pytest.raises(ValueError, match="never been published"):
        db.create_chat_session(username="user", project_name="draft-only", start_state="start")


@pytest.mark.regression
def test_create_chat_session_allow_draft_permits_an_unpublished_project(db):
    db.ensure_project("draft-only")
    session_id = db.create_chat_session(
        username="user", project_name="draft-only", start_state="start", allow_draft=True
    )
    session = db.get_chat_session(session_id)
    assert session is not None


@pytest.mark.regression
def test_create_chat_session_allow_draft_stamps_the_current_draft_revision_not_published(db):
    # Publish once (revision 0), then edit again so the draft moves ahead
    # to revision 1 while published_revision stays frozen at 0 — a
    # allow_draft session must be stamped with the *draft* (1), unlike a
    # normal session, which would be stamped with published_revision (0).
    db.ensure_project("ahead-of-published")
    db.publish_project("ahead-of-published")
    db.save_project_file("user", "ahead-of-published", "index.yml", "states: {}\n")

    draft_session_id = db.create_chat_session(
        username="user", project_name="ahead-of-published", start_state="start", allow_draft=True
    )
    normal_session_id = db.create_chat_session(
        username="user", project_name="ahead-of-published", start_state="start"
    )

    assert db.get_project_revision("ahead-of-published") == 1
    assert db.get_project_published_revision("ahead-of-published") == 0

    # project_revision isn't in the public dict (see _chat_session_to_dict) —
    # read it straight off the model instead.
    from db.models import ChatSession
    assert ChatSession.get_by_id(draft_session_id).project_revision == 1
    assert ChatSession.get_by_id(normal_session_id).project_revision == 0


@pytest.mark.regression
def test_get_latest_chat_session_picks_most_recent_start(db):
    _make_session(db, start=datetime(2026, 1, 1, 9, 0, 0))
    newer = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))

    latest = db.get_latest_chat_session("user", "proj")

    assert latest["id"] == newer


@pytest.mark.regression
def test_get_latest_chat_session_scoped_by_username_and_project(db):
    _make_session(db, username="user", project_name="proj-a", start=datetime(2026, 1, 1, 12, 0, 0))

    assert db.get_latest_chat_session("user", "proj-b") is None
    assert db.get_latest_chat_session("other-user", "proj-a") is None


@pytest.mark.regression
def test_touch_chat_session_updates_end_only(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    new_end = datetime(2026, 1, 1, 10, 30, 0)

    db.touch_chat_session(session_id, new_end, "next")
    session = db.get_chat_session(session_id)

    assert session["datetime_end"] == new_end
    assert session["end_state"] == "next"
    assert session["start_state"] == "start"


@pytest.mark.regression
def test_save_message_requires_a_session(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))

    db.save_message("user", "hello", session_id)
    messages = db.get_messages(session_id)

    assert [m["content"] for m in messages] == ["hello"]


@pytest.mark.regression
def test_get_messages_scoped_by_session_not_project(db):
    s1 = _make_session(db, start=datetime(2026, 1, 1, 9, 0, 0))
    s2 = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.save_message("user", "in session 1", s1)
    db.save_message("user", "in session 2", s2)

    assert [m["content"] for m in db.get_messages(s1)] == ["in session 1"]
    assert [m["content"] for m in db.get_messages(s2)] == ["in session 2"]


@pytest.mark.regression
def test_reset_project_deletes_sessions_and_their_messages(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.save_message("user", "hello", session_id)

    db.reset_project("proj")

    assert db.get_chat_session(session_id) is None
    assert db.get_messages(session_id) == []


@pytest.mark.regression
def test_reset_project_wipes_every_user_regardless(db):
    mine = _make_session(db, username="user", start=datetime(2026, 1, 1, 10, 0, 0))
    theirs = _make_session(db, username="other-user", start=datetime(2026, 1, 1, 11, 0, 0))

    db.reset_project("proj")

    assert db.get_chat_session(mine) is None
    assert db.get_chat_session(theirs) is None


@pytest.mark.regression
def test_reset_project_for_user_only_touches_that_user(db):
    mine = _make_session(db, username="user", project_name="proj", start=datetime(2026, 1, 1, 10, 0, 0))
    theirs = _make_session(db, username="other-user", project_name="proj", start=datetime(2026, 1, 1, 11, 0, 0))
    db.save_message("user", "hello", mine)
    db.save_message("other-user", "hi", theirs)

    db.reset_project_for_user("user", "proj")

    assert db.get_chat_session(mine) is None
    assert db.get_messages(mine) == []
    assert db.get_chat_session(theirs) is not None
    assert [m["content"] for m in db.get_messages(theirs)] == ["hi"]


@pytest.mark.regression
def test_reset_project_for_user_only_touches_that_project(db):
    session_a = _make_session(db, username="user", project_name="proj-a", start=datetime(2026, 1, 1, 10, 0, 0))
    session_b = _make_session(db, username="user", project_name="proj-b", start=datetime(2026, 1, 1, 10, 0, 0))

    db.reset_project_for_user("user", "proj-a")

    assert db.get_chat_session(session_a) is None
    assert db.get_chat_session(session_b) is not None


@pytest.mark.regression
def test_delete_chat_session_removes_it_and_its_data(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.save_message("user", "hello", session_id)
    db.save_transition("", "init", "start", session_id, transition_log_level="INFO")

    db.delete_chat_session(session_id)

    assert db.get_chat_session(session_id) is None
    assert db.get_messages(session_id) == []
    assert db.get_current_state("proj") is None


@pytest.mark.regression
def test_delete_chat_session_does_not_touch_other_sessions(db):
    keep = _make_session(db, start=datetime(2026, 1, 1, 9, 0, 0))
    doomed = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.save_message("user", "keep me", keep)
    db.save_message("user", "delete me", doomed)

    db.delete_chat_session(doomed)

    assert db.get_chat_session(keep) is not None
    assert [m["content"] for m in db.get_messages(keep)] == ["keep me"]


@pytest.mark.contract
def test_foreign_key_cascade_is_enforced_at_the_sqlite_level(db):
    """Reinforces db.delete_chat_session's explicit ordered deletes:
    even a raw ChatSession delete (bypassing delete_chat_session
    entirely) must cascade to Message on its own, proving PRAGMA
    foreign_keys + ON DELETE CASCADE (see db._enable_foreign_keys and
    Message.session) are actually both in effect for this connection."""
    from db.models import ChatSession, Message

    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.save_message("user", "hello", session_id)

    ChatSession.delete().where(ChatSession.id == session_id).execute()

    assert Message.select().where(Message.session == session_id).count() == 0


@pytest.mark.regression
def test_session_has_annotations_is_false_with_no_signals_at_all(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    assert db.session_has_annotations(session_id) is False


@pytest.mark.regression
def test_session_has_annotations_is_false_when_nothing_is_annotated(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    message_id = db.save_message("user", "hi", session_id)
    db.save_signal_snapshot({"foo": 1}, session_id, message_id=message_id)
    assert db.session_has_annotations(session_id) is False


@pytest.mark.regression
def test_session_has_annotations_is_true_with_an_expected_state(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    message_id = db.save_message("user", "hi", session_id)
    row_id = db.save_signal_snapshot({"foo": 1}, session_id, message_id=message_id)
    db.set_signal_expected_state(row_id, "start")
    assert db.session_has_annotations(session_id) is True


@pytest.mark.regression
def test_session_has_annotations_is_true_with_expected_values(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    message_id = db.save_message("user", "hi", session_id)
    row_id = db.save_signal_snapshot({"foo": 1}, session_id, message_id=message_id)
    db.set_signal_expected_values(row_id, {"foo": 75})
    assert db.session_has_annotations(session_id) is True


@pytest.mark.regression
def test_get_annotated_session_ids_scoped_to_username_and_project(db):
    annotated = _make_session(db, username="user", project_name="proj", start=datetime(2026, 1, 1, 10, 0, 0))
    unannotated = _make_session(db, username="user", project_name="proj", start=datetime(2026, 1, 2, 10, 0, 0))
    other_project = _make_session(db, username="user", project_name="other", start=datetime(2026, 1, 3, 10, 0, 0))
    other_user = _make_session(db, username="someone-else", project_name="proj", start=datetime(2026, 1, 4, 10, 0, 0))

    for session_id in (annotated, unannotated, other_project, other_user):
        message_id = db.save_message("user", "hi", session_id)
        row_id = db.save_signal_snapshot({"foo": 1}, session_id, message_id=message_id)
        if session_id != unannotated:
            db.set_signal_expected_state(row_id, "start")

    assert db.get_annotated_session_ids("user", "proj") == {annotated}
