from __future__ import annotations

from datetime import datetime

import pytest

from db.models import ChatSession, Message


def _make_session(db, *, username="user", project_name="proj", start=datetime(2026, 1, 1, 10, 0, 0), end=None, start_state="start", end_state=None, **kwargs):
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
        **kwargs,
    )


@pytest.mark.contract
def test_create_and_get_chat_session_with_the_channel_defaulting_to_native_chat_and_none_for_an_unknown_id(db):
    session_id = _make_session(db)

    session = db.get_chat_session(session_id)
    assert session["username"] == "user"
    assert session["project_id"] == "proj"
    assert session["start_state"] == "start"
    assert session["end_state"] == "start"
    assert session["channel"] == "native-chat"

    assert db.get_chat_session(_make_session(db, channel="whatsapp-chat"))["channel"] == "whatsapp-chat"
    assert db.get_chat_session(999999) is None


@pytest.mark.regression
def test_create_chat_session_rejects_a_nonexistent_project_and_stamps_whatever_revision_the_caller_resolved(db):
    # Revision resolution (published vs. draft) lives one layer up now —
    # see chat.session_type_strategy.SessionTypeStrategy.revision_for —
    # create_chat_session itself just stamps whatever it's given.
    with pytest.raises(ValueError, match="does not exist"):
        db.create_chat_session("user", "no-such-project", 0, start_state="start")

    db.ensure_project("ahead-of-published")
    db.publish_project("ahead-of-published")
    db.save_project_file("user", "ahead-of-published", "index.yml", b"states: {}\n", "text/yaml")
    assert db.get_project_revision("ahead-of-published") == 1
    assert db.get_project_published_revision("ahead-of-published") == 0

    draft_session_id = db.create_chat_session(
        "user", "ahead-of-published", db.get_project_revision("ahead-of-published"), start_state="start", type="test",
    )
    normal_session_id = db.create_chat_session(
        "user", "ahead-of-published", db.get_project_published_revision("ahead-of-published"), start_state="start",
    )

    # project_revision isn't in the public dict (see _chat_session_to_dict) —
    # read it straight off the model instead.
    assert ChatSession.get_by_id(draft_session_id).project_revision == 1
    assert ChatSession.get_by_id(normal_session_id).project_revision == 0


@pytest.mark.regression
def test_the_default_title_counts_per_type_and_username_and_never_overrides_an_explicit_one(db):
    first = _make_session(db, start=datetime(2026, 1, 1, 9, 0, 0))
    second = _make_session(db)
    other_user = _make_session(db, username="other-user")
    explicit = _make_session(db, title="Renamed")
    test_session = db.create_chat_session("user", "proj", db.get_project_published_revision("proj"), start_state="start", type="test")

    assert db.get_chat_session(first)["title"] == "Live session 1"
    assert db.get_chat_session(second)["title"] == "Live session 2"
    assert db.get_chat_session(test_session)["title"] == "Test session 1"
    assert db.get_chat_session(other_user)["title"] == "Live session 1"
    assert db.get_chat_session(explicit)["title"] == "Renamed"


@pytest.mark.regression
def test_get_latest_chat_session_picks_the_most_recent_start_scoped_by_username_and_project(db):
    _make_session(db, start=datetime(2026, 1, 1, 9, 0, 0))
    newer = _make_session(db)
    _make_session(db, project_name="proj-a", start=datetime(2026, 1, 1, 12, 0, 0))

    assert db.get_latest_chat_session("user", "proj")["id"] == newer
    assert db.get_latest_chat_session("user", "proj-b") is None
    assert db.get_latest_chat_session("other-user", "proj-a") is None


@pytest.mark.regression
def test_touch_updates_only_the_end_and_messages_are_scoped_by_session_not_project(db):
    s1 = _make_session(db, start=datetime(2026, 1, 1, 9, 0, 0))
    s2 = _make_session(db)
    new_end = datetime(2026, 1, 1, 10, 30, 0)

    db.touch_chat_session(s1, new_end, "next")
    session = db.get_chat_session(s1)
    assert session["datetime_end"] == new_end
    assert session["end_state"] == "next"
    assert session["start_state"] == "start"

    db.save_message("user", "in session 1", s1)
    db.save_message("user", "in session 2", s2)
    assert [m["content"] for m in db.get_messages(s1)] == ["in session 1"]
    assert [m["content"] for m in db.get_messages(s2)] == ["in session 2"]


@pytest.mark.regression
def test_reset_project_deletes_every_users_sessions_and_messages_while_reset_for_user_touches_only_theirs_in_that_project(db):
    mine = _make_session(db)
    theirs = _make_session(db, username="other-user", start=datetime(2026, 1, 1, 11, 0, 0))
    elsewhere = _make_session(db, project_name="proj-b")
    db.save_message("user", "hello", mine)
    db.save_message("other-user", "hi", theirs)

    db.reset_project_for_user("user", "proj", type="live")
    assert db.get_chat_session(mine) is None
    assert db.get_messages(mine) == []
    assert db.get_chat_session(theirs) is not None
    assert [m["content"] for m in db.get_messages(theirs)] == ["hi"]
    assert db.get_chat_session(elsewhere) is not None

    db.reset_project("proj")
    assert db.get_chat_session(theirs) is None
    assert db.get_messages(theirs) == []
    assert db.get_chat_session(elsewhere) is not None


@pytest.mark.regression
def test_delete_chat_session_removes_it_and_its_data_leaving_other_sessions_alone_with_the_fk_cascade_enforced_by_sqlite(db):
    """A raw ChatSession delete (bypassing delete_chat_session) must still
    cascade to Message, proving FK enforcement is on for this connection."""
    keep = _make_session(db, start=datetime(2026, 1, 1, 9, 0, 0))
    doomed = _make_session(db)
    raw = _make_session(db, start=datetime(2026, 1, 1, 11, 0, 0))
    db.save_message("user", "keep me", keep)
    db.save_message("user", "delete me", doomed)
    db.save_message("user", "raw", raw)
    db.save_transition("", "init", "start", doomed, transition_log_level="INFO")

    db.delete_chat_session(doomed)
    assert db.get_chat_session(doomed) is None
    assert db.get_messages(doomed) == []
    assert db.get_current_state("proj") is None
    assert db.get_chat_session(keep) is not None
    assert [m["content"] for m in db.get_messages(keep)] == ["keep me"]

    ChatSession.delete().where(ChatSession.id == raw).execute()
    assert Message.select().where(Message.session == raw).count() == 0


@pytest.mark.regression
def test_set_session_labeled_toggles_only_the_given_session_starting_unlabeled(db):
    marked = _make_session(db)
    untouched = _make_session(db, start=datetime(2026, 1, 2, 10, 0, 0))
    assert db.get_chat_session(marked)["labeled"] is False

    db.set_session_labeled(marked, True)
    assert db.get_chat_session(marked)["labeled"] is True
    assert db.get_chat_session(untouched)["labeled"] is False

    db.set_session_labeled(marked, False)
    assert db.get_chat_session(marked)["labeled"] is False
