"""AtomicTurnTransaction (turn/atomic_turn_transaction.py) is the DB as an
exchange sees it: it answers every read from the persisted rows plus the
ones the exchange has produced so far, and touches the DB only in its
commit — one `atomic()` that lands the rows with the timestamps they were
created with. Its discard leaves the DB exactly as it found it.

These tests drive the object through its own public methods, the same
ones TurnService's exchanges and the turn's collaborators use, and observe
the DB through Db's own reads.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from turn.atomic_turn_transaction import AtomicTurnTransaction
from turn.turn_transaction import Inbox

pytestmark = pytest.mark.contract

PROJECT_ID = "proj"


def _session(db) -> int:
    db.ensure_project(PROJECT_ID)
    db.publish_project(PROJECT_ID)
    return db.create_chat_session("user", PROJECT_ID, db.get_project_revision(PROJECT_ID))


def _exchange(db, session_id: int, *texts: str) -> tuple[AtomicTurnTransaction, Inbox]:
    inbox = Inbox(session_id)
    answering = [inbox.accept(text) for text in texts]
    return AtomicTurnTransaction(db, session_id, answering, inbox), inbox


def test_nothing_reaches_the_db_before_the_commit_and_everything_lands_at_once(db):
    session_id = _session(db)
    transaction, inbox = _exchange(db, session_id, "hello")
    reply = transaction.save_message("assistant", "hi there", session_id, tokens=3)
    transition = transaction.save_transition("a", "go", "b", session_id, transition_log_level="INFO", origin="trigger")
    transaction.link_signal_to_message(transition, reply)
    transaction.mark_messages_answered(transaction.answering, reply)
    transaction.set_action_env(session_id, {"steps": 1})

    assert db.get_messages(session_id) == []
    assert db.get_signals(session_id) == []
    assert db.get_current_state_for_session(session_id) is None
    assert [m["content"] for m in inbox.pending()] == ["hello"]

    transaction.commit()

    messages = db.get_messages(session_id)
    assert [(m["role"], m["content"]) for m in messages] == [("user", "hello"), ("assistant", "hi there")]
    assert db.get_turn_history(session_id, None, None)[0]["answered_by"] == messages[1]["id"]
    assert db.get_current_state_for_session(session_id) == "b"
    assert db.get_signals(session_id)[0]["message_id"] == messages[1]["id"]
    assert db.get_action_env(PROJECT_ID, "user") == {"steps": 1}
    assert inbox.pending() == []


def test_a_discarded_exchange_leaves_the_db_and_the_inbox_as_they_were(db):
    session_id = _session(db)
    transaction, inbox = _exchange(db, session_id, "hello")
    transaction.save_message("assistant", "hi there", session_id)
    transaction.save_transition("a", "go", "b", session_id, transition_log_level="INFO", origin="trigger")
    transaction.set_env(session_id, {"note": "x"})

    transaction.discard()

    assert db.get_messages(session_id) == []
    assert db.get_signals(session_id) == []
    assert db.get_env(PROJECT_ID, "user") == {}
    assert inbox.pending() == []


def test_the_exchange_reads_its_own_rows_as_the_db_would(db):
    session_id = _session(db)
    transaction, _ = _exchange(db, session_id, "one", "two")

    transition = transaction.save_transition("a", "go", "a", session_id, transition_log_level="INFO", origin="manual")
    transaction.set_action_env(session_id, {"steps": 2})
    transaction.set_env(session_id, {"memory": "kept"})
    transaction.set_local_memory(session_id, {"local": "yes"})

    assert transaction.get_last_entry_timestamp_for_session(session_id, "a") == transition.timestamp
    assert transaction.get_action_env(PROJECT_ID, "user") == {"steps": 2}
    assert transaction.get_env(PROJECT_ID, "user") == {"memory": "kept"}
    assert transaction.get_local_memory(session_id) == {"local": "yes"}
    assert [row["new_state"] for row in transaction.get_signals(session_id)] == ["a"]
    assert transaction.get_turn_history(session_id, None, None) == [
        {**transaction.answering[-1].as_dict(), "content": ["one", "two"]},
    ]
    assert [m["content"] for m in transaction.get_messages(session_id)] == ["one", "two"]
    assert transaction.has_messages_since(session_id, None) is True
    assert transaction.has_assistant_message_since(session_id, None) is False
    assert db.get_signals(session_id) == []


def test_rows_land_with_the_timestamps_they_were_created_with_in_creation_order(db):
    session_id = _session(db)
    transaction, _ = _exchange(db, session_id, "hello")
    first = transaction.save_transition("", "init", "a", session_id, transition_log_level="INFO", origin="manual")
    first.timestamp = first.timestamp - timedelta(minutes=2)
    later = transaction.set_action_env(session_id, {"steps": 1})
    later.timestamp = later.timestamp - timedelta(minutes=1)
    reply = transaction.save_message("assistant", "hi", session_id)

    transaction.commit()

    assert db.get_last_entry_timestamp_for_session(session_id, "a") == first.timestamp
    assert db.get_action_env(PROJECT_ID, "user", until=later.timestamp) == {"steps": 1}
    assert db.get_action_env(PROJECT_ID, "user", until=first.timestamp) == {}
    assert db.get_message(reply.id)["content"] == "hi"


def test_an_ai_opened_exchange_asks_with_the_placeholder_user_turn(db):
    session_id = _session(db)
    transaction = AtomicTurnTransaction(db, session_id, [], Inbox(session_id))

    assert transaction.get_turn_history(session_id, None, None) == [{"role": "user", "content": "..."}]
