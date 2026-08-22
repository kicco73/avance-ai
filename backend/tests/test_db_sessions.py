from __future__ import annotations

from datetime import datetime

import pytest


def _make_session(db, *, username="user", project_name="proj", start, end=None, start_state="start", end_state=None):
    end = end if end is not None else start
    end_state = end_state if end_state is not None else start_state
    db.ensure_project(project_name)
    db.publish_project(project_name)
    revision = db.get_project_published_revision(project_name)
    return db.create_chat_session(
        username, project_name, revision,
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
def test_create_chat_session_rejects_a_nonexistent_project(db):
    with pytest.raises(ValueError, match="does not exist"):
        db.create_chat_session("user", "no-such-project", 0, start_state="start")


@pytest.mark.regression
def test_create_chat_session_stamps_whatever_revision_the_caller_resolved(db):
    # Revision resolution (published vs. draft) lives one layer up now —
    # see chat.session_type_strategy.SessionTypeStrategy.revision_for —
    # create_chat_session itself just stamps whatever it's given.
    db.ensure_project("ahead-of-published")
    db.publish_project("ahead-of-published")
    db.save_project_file("user", "ahead-of-published", "index.yml", b"states: {}\n", "text/yaml")

    draft_session_id = db.create_chat_session(
        "user", "ahead-of-published", db.get_project_revision("ahead-of-published"),
        start_state="start", type="test",
    )
    normal_session_id = db.create_chat_session(
        "user", "ahead-of-published", db.get_project_published_revision("ahead-of-published"),
        start_state="start",
    )

    assert db.get_project_revision("ahead-of-published") == 1
    assert db.get_project_published_revision("ahead-of-published") == 0

    # project_revision isn't in the public dict (see _chat_session_to_dict) —
    # read it straight off the model instead.
    from db.models import ChatSession
    assert ChatSession.get_by_id(draft_session_id).project_revision == 1
    assert ChatSession.get_by_id(normal_session_id).project_revision == 0


@pytest.mark.regression
def test_create_chat_session_defaults_the_title_to_type_and_count(db):
    first_id = _make_session(db, start=datetime(2026, 1, 1, 9, 0, 0))
    second_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))

    assert db.get_chat_session(first_id)["title"] == "Live session 1"
    assert db.get_chat_session(second_id)["title"] == "Live session 2"


@pytest.mark.regression
def test_create_chat_session_default_title_never_overrides_an_explicit_one(db):
    db.ensure_project("proj")
    db.publish_project("proj")
    revision = db.get_project_published_revision("proj")

    session_id = db.create_chat_session("user", "proj", revision, start_state="start", title="Renamed")

    assert db.get_chat_session(session_id)["title"] == "Renamed"


@pytest.mark.regression
def test_create_chat_session_default_title_counts_are_independent_per_type_and_username(db):
    db.ensure_project("proj")
    db.publish_project("proj")
    revision = db.get_project_published_revision("proj")

    live_id = db.create_chat_session("user", "proj", revision, start_state="start", type="live")
    test_id = db.create_chat_session("user", "proj", revision, start_state="start", type="test")
    other_user_live_id = db.create_chat_session("other-user", "proj", revision, start_state="start", type="live")

    assert db.get_chat_session(live_id)["title"] == "Live session 1"
    assert db.get_chat_session(test_id)["title"] == "Test session 1"
    assert db.get_chat_session(other_user_live_id)["title"] == "Live session 1"


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

    db.reset_project_for_user("user", "proj", type="live")

    assert db.get_chat_session(mine) is None
    assert db.get_messages(mine) == []
    assert db.get_chat_session(theirs) is not None
    assert [m["content"] for m in db.get_messages(theirs)] == ["hi"]


@pytest.mark.regression
def test_reset_project_for_user_only_touches_that_project(db):
    session_a = _make_session(db, username="user", project_name="proj-a", start=datetime(2026, 1, 1, 10, 0, 0))
    session_b = _make_session(db, username="user", project_name="proj-b", start=datetime(2026, 1, 1, 10, 0, 0))

    db.reset_project_for_user("user", "proj-a", type="live")

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
    """A raw ChatSession delete (bypassing delete_chat_session) must still
    cascade to Message, proving FK enforcement is on for this connection."""
    from db.models import ChatSession, Message

    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.save_message("user", "hello", session_id)

    ChatSession.delete().where(ChatSession.id == session_id).execute()

    assert Message.select().where(Message.session == session_id).count() == 0


@pytest.mark.regression
def test_a_new_session_starts_unlabeled(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    assert db.get_chat_session(session_id)["labeled"] is False


@pytest.mark.regression
def test_set_session_labeled_marks_a_session_done(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.set_session_labeled(session_id, True)
    assert db.get_chat_session(session_id)["labeled"] is True


@pytest.mark.regression
def test_set_session_labeled_can_toggle_back_off(db):
    session_id = _make_session(db, start=datetime(2026, 1, 1, 10, 0, 0))
    db.set_session_labeled(session_id, True)
    db.set_session_labeled(session_id, False)
    assert db.get_chat_session(session_id)["labeled"] is False


@pytest.mark.regression
def test_set_session_labeled_only_touches_the_given_session(db):
    marked = _make_session(db, username="user", project_name="proj", start=datetime(2026, 1, 1, 10, 0, 0))
    untouched = _make_session(db, username="user", project_name="proj", start=datetime(2026, 1, 2, 10, 0, 0))

    db.set_session_labeled(marked, True)

    assert db.get_chat_session(marked)["labeled"] is True
    assert db.get_chat_session(untouched)["labeled"] is False
