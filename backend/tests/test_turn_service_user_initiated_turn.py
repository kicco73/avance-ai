"""TurnService.prepare_user_initiated_turn reports what it wrote.

A turn arriving from a channel with no transcript to read opens the
session itself, and the wrap-up message that opening writes is part of
the answer that person is owed. It is not in the turn's own reply: a
turn response carries exactly one assistant message, its own (see
TrackingProcessor._build_turn_response). WhatsApp used to find the other
one by watermarking the last message id and reading every assistant row
persisted since — which a consumer holding only the result, a Bus
listener on the finished answer, could never do.
"""
from __future__ import annotations

import pytest

from test_turn_service_prepare_user_initiated_turn import _automaton, _turn_service

pytestmark = pytest.mark.contract


def _assistant_ids(db, session_id: int) -> list[int]:
    return [m["id"] for m in db.get_messages(session_id) if m["role"] == "assistant"]


async def test_a_chat_blocked_state_reports_the_wrap_up_the_turn_itself_never_will(db):
    """The case the watermark existed for. The state cannot take a turn,
    so the only thing this session will ever say is written by the
    preparation — and process_turn's own reply does not contain it."""
    turn_service = _turn_service(db, _automaton(final=True))
    session = await turn_service.get_current_session_if_any_or_create_new(None)

    prepared = await turn_service.prepare_user_initiated_turn(session["id"])
    result = await turn_service.process_turn(session["id"], "ciao")

    reported = [m["id"] for m in (*prepared, *result["reply"])]
    assert reported == _assistant_ids(db, session["id"])
    assert len(reported) == 2


async def test_the_turn_alone_reports_only_half_of_it(db):
    """Stated as its own fact, because it is the reason the method
    exists: if this ever stops being true, the merge above is dead
    weight rather than a fix."""
    turn_service = _turn_service(db, _automaton(final=True))
    session = await turn_service.get_current_session_if_any_or_create_new(None)

    await turn_service.prepare_user_initiated_turn(session["id"])
    result = await turn_service.process_turn(session["id"], "ciao")

    assert len(result["reply"]) == 1
    assert len(_assistant_ids(db, session["id"])) == 2


async def test_an_ordinary_state_prepares_nothing_at_all(db):
    """Nothing to report, and nothing written: a state that can take a
    turn says what it has to say in the turn."""
    turn_service = _turn_service(db, _automaton(final=False))
    session = await turn_service.get_current_session_if_any_or_create_new(None)

    prepared = await turn_service.prepare_user_initiated_turn(session["id"])
    result = await turn_service.process_turn(session["id"], "ciao")

    assert prepared == []
    assert [m["id"] for m in result["reply"]] == _assistant_ids(db, session["id"])


async def test_the_prepared_message_was_written_before_the_turn_s_own(db):
    """A channel sends them in the order it was given them, so the order
    has to be the order they happened in."""
    turn_service = _turn_service(db, _automaton(final=True))
    session = await turn_service.get_current_session_if_any_or_create_new(None)

    prepared = await turn_service.prepare_user_initiated_turn(session["id"])
    result = await turn_service.process_turn(session["id"], "ciao")

    assert prepared[-1]["id"] < result["reply"][0]["id"]
