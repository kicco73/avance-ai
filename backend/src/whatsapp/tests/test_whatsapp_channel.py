from __future__ import annotations

from http import HTTPStatus

import pytest

from system import bus
from system.bus import SESSION_ENDED, SESSION_OPENED, Message
from system.service_error import ServiceError
from whatsapp import notices
from whatsapp.conversation import ACCEPT_TERMS
from whatsapp.tests.whatsapp_helpers import (  # noqa: F401 — env is a fixture
    LINKED_EMAIL, LINKED_NUMBER, PROJECT, SESSION_ID, UNKNOWN_NUMBER, Env, _action, _config,
    _interactive_payload, _payload, env,
)

REACHES_INTO = {
    "_conversations": "owned by another session's rewrite",
}

pytestmark = pytest.mark.contract

REPLY_TEXT = "*Hola* — has dicho: hola"


def _conflict(code: str, message: str = "Session is not active.") -> ServiceError:
    return ServiceError(message, status_code=HTTPStatus.CONFLICT, code=code)


async def test_a_typed_message_enters_the_conversation_and_its_reply_comes_back_as_text(env: Env):
    await env.arrives(_payload(text="hola"))

    assert env.turns.calls == [("enter", PROJECT, "live"), ("turn", SESSION_ID, "hola")]
    assert env.api.read == ["wamid.1"]
    assert env.api.sent == [(LINKED_NUMBER, REPLY_TEXT)]
    assert env.api.timeline == ["typing", "text"]


async def test_this_channel_never_writes_the_first_message(env: Env):
    """A conversation opening is a fact core announces and a chat
    answers. Nothing here answers it: a WhatsApp conversation only ever
    starts because somebody wrote."""
    assert bus.handlers_for(SESSION_OPENED) == []

    await env.arrives(_payload(text="hola"))

    assert env.api.bodies == [REPLY_TEXT]
    assert [role for role, in [(m["role"],) for m in env.db.messages]] == ["user", "assistant"]


async def test_what_the_state_owed_arrives_before_the_answer(env: Env):
    env.turns.wrap_up_message = "Conversación finalizada."
    await env.arrives(_payload(text="hola"))

    assert env.api.bodies == ["Conversación finalizada.", REPLY_TEXT]


async def test_no_typing_indicator_when_mark_read_is_off():
    env = Env(config=_config(mark_read=False))
    await env.arrives(_payload(text="hola"))

    assert env.api.read == []
    assert env.api.timeline == ["text"]


async def test_two_messages_one_after_the_other_each_get_their_own_answer(env: Env):
    await env.arrives(_payload(msg_id="wamid.1", text="hola"))
    await env.arrives(_payload(msg_id="wamid.2", text="con el vuelo VY3003"))

    assert [call for call in env.turns.calls if call[0] == "turn"] == [
        ("turn", SESSION_ID, "hola"), ("turn", SESSION_ID, "con el vuelo VY3003"),
    ]
    assert env.api.bodies == [REPLY_TEXT, "*Hola* — has dicho: con el vuelo VY3003"]


async def test_a_session_open_on_another_channel_is_taken_over_before_the_turn(env: Env):
    env.turns.session = {"id": SESSION_ID, "channel": "webchat", "project_id": PROJECT}

    await env.arrives(_payload(text="hola"))

    assert env.turns.calls == [
        ("enter", PROJECT, "live"), ("create", PROJECT, "live"), ("turn", SESSION_ID + 1, "hola"),
    ]
    assert env.api.sent == [(LINKED_NUMBER, REPLY_TEXT)]


async def test_a_conversation_closed_from_elsewhere_is_forgotten_rather_than_still_watched(env: Env):
    """Taking it back is the other channel's to do, and when it does, the
    session this one holds is closed under it. Holding on to the id would
    leave this conversation being handed announcements about a
    conversation that is no longer its own."""
    await env.arrives(_payload(text="hola"))
    conversation = env.service._conversations[LINKED_NUMBER]
    assert conversation.watching(SESSION_ID)

    await bus.publish(Message(
        type=SESSION_ENDED, username=LINKED_EMAIL, session_id=SESSION_ID,
        body={"reason": "channel-switch"},
    ))

    assert not conversation.watching(SESSION_ID)


async def test_the_next_message_after_a_takeover_opens_a_conversation_of_its_own(env: Env):
    """Symmetry with the other channel: whoever the person writes to
    takes the conversation, and takes it by opening a new one."""
    await env.arrives(_payload(msg_id="wamid.1", text="hola"))
    env.turns.session = {"id": SESSION_ID, "channel": "webchat", "project_id": PROJECT}

    await env.arrives(_payload(msg_id="wamid.2", text="otra vez"))

    assert [call for call in env.turns.calls if call[0] in ("create", "turn")] == [
        ("turn", SESSION_ID, "hola"),
        ("create", PROJECT, "live"),
        ("turn", SESSION_ID + 1, "otra vez"),
    ]


async def test_a_paused_project_is_a_refusal_not_a_turn(env: Env):
    env.turns.session = {"blocked": "paused", "detail": "quota"}

    await env.arrives(_payload(text="hola"))

    assert [call for call in env.turns.calls if call[0] == "turn"] == []
    assert env.api.sent == [(LINKED_NUMBER, notices.PAUSED)]


async def test_an_unlinked_number_an_unregistered_account_and_an_unreadable_message_each_get_a_notice():
    env = Env()
    await env.arrives(_payload(sender=UNKNOWN_NUMBER, mtype="audio"))
    assert env.turns.calls == []
    assert env.api.sent == [(UNKNOWN_NUMBER, notices.NOT_LINKED)]

    env = Env()
    await env.arrives(_payload(sender=UNKNOWN_NUMBER, text="NOTACODE"))
    assert env.turns.calls == []
    assert env.api.sent == [(UNKNOWN_NUMBER, "This invite code is unknown.")]

    env = Env()
    env.db.users[LINKED_NUMBER]["role"] = None
    await env.arrives(_payload())
    assert env.turns.calls == []
    assert env.api.sent == [(LINKED_NUMBER, notices.NOT_REGISTERED)]

    env = Env()
    await env.arrives(_payload(mtype="image"))
    assert env.turns.calls == []
    assert env.api.sent == [(LINKED_NUMBER, notices.UNSUPPORTED)]


async def test_a_valid_invite_code_registers_and_says_so_without_starting_the_conversation():
    """The web greets a fresh registration with whatever the project
    opens with. Here nothing does: the code was not something to answer,
    and this channel never speaks first."""
    env = Env()
    env.auth.valid_codes["GOODCODE"] = PROJECT

    await env.arrives(_payload(sender=UNKNOWN_NUMBER, text="GOODCODE"))

    assert env.db.users[UNKNOWN_NUMBER]["role"] == "user"
    assert env.turns.calls == []
    assert env.api.sent == [(UNKNOWN_NUMBER, notices.REGISTERED)]

    env = Env()
    env.auth.valid_codes["GOODCODE"] = PROJECT
    await env.arrives(_payload(sender=UNKNOWN_NUMBER, text="Invitation code: GOODCODE"))
    assert env.db.users[UNKNOWN_NUMBER]["role"] == "user"


async def test_an_unexpected_failure_gets_an_apology_not_silence(env: Env):
    env.auth.unexpected_error = RuntimeError("boom")

    await env.arrives(_payload(sender=UNKNOWN_NUMBER, text="GOODCODE"))

    assert env.turns.calls == []
    assert env.api.sent == [(UNKNOWN_NUMBER, notices.TECHNICAL_PROBLEM)]
    assert UNKNOWN_NUMBER not in env.db.users


async def test_pending_terms_send_the_content_with_an_accept_button_instead_of_a_turn(env: Env):
    env.turns.session = {"legal_terms_pending": True, "project_id": PROJECT}
    env.turns.terms_content = "## Terms\n\nBe nice."

    await env.arrives(_payload(text="hola"))

    assert [call for call in env.turns.calls if call[0] == "turn"] == []
    assert env.api.sent == []
    kind, to, body, buttons = env.api.interactive[0]
    assert kind == "button" and to == LINKED_NUMBER
    assert body == "*Terms*\n\nBe nice."
    assert buttons == [(ACCEPT_TERMS, notices.ACCEPT_TERMS_LABEL)]


async def test_accepting_the_terms_confirms_and_leaves_the_next_word_to_the_person(env: Env):
    env.turns.session = {"legal_terms_pending": True, "project_id": PROJECT}
    await env.arrives(_payload(text="hola"))

    await env.arrives(_interactive_payload(msg_id="wamid.2", reply={"id": ACCEPT_TERMS, "title": "Accept"}))

    assert env.turns.accepted_terms_for == [PROJECT]
    assert env.api.bodies == [notices.TERMS_ACCEPTED]
    assert [call for call in env.turns.calls if call[0] in ("turn", "action")] == []


async def test_the_choices_ride_on_the_reply_as_buttons_or_as_a_list(env: Env):
    env.turns.buttons = [_action("go", "Go"), _action("stay", "Stay")]
    await env.arrives(_payload(text="hola"))
    assert env.api.sent == []
    assert env.api.interactive == [("button", LINKED_NUMBER, REPLY_TEXT, [("go", "Go"), ("stay", "Stay")])]

    env = Env()
    env.turns.buttons = [_action(f"a{i}", f"Action {i}", ui_description=f"Does {i}") for i in range(5)]
    await env.arrives(_payload(text="hola"))
    kind, to, body, button_text, rows = env.api.interactive[0]
    assert kind == "list" and to == LINKED_NUMBER and button_text == "Options"
    assert len(rows) == 5 and rows[0] == ("a0", "Action 0", "Does 0")


async def test_a_long_button_title_is_truncated_and_no_choices_means_plain_text():
    env = Env()
    env.turns.buttons = [_action("go", "A very very long button label indeed")]
    await env.arrives(_payload(text="hola"))
    assert env.api.interactive[0][3] == [("go", "A very very long bu…")]
    assert len(env.api.interactive[0][3][0][1]) == 20

    env = Env()
    await env.arrives(_payload(text="hola"))
    assert env.api.sent == [(LINKED_NUMBER, REPLY_TEXT)]
    assert env.api.interactive == []


async def test_the_choices_offered_while_entering_are_never_sent_on_their_own(env: Env):
    """Entering answers with what the state offers, the same way it does
    for a browser. A phone is not showing a screen to update: those
    choices are remembered, and only what the exchange itself offers is
    sent."""
    env.turns.buttons = [_action("go", "Go")]

    await env.arrives(_payload(text="hola"))

    assert len(env.api.interactive) == 1
    assert env.api.interactive[0][2] == REPLY_TEXT


async def test_a_tapped_button_applies_the_action_and_the_new_choices_ride_on_its_message(env: Env):
    env.turns.buttons = [_action("stay", "Stay")]
    env.turns.action_reply_message = "You picked go."

    await env.arrives(_interactive_payload(kind="button_reply", reply={"id": "go", "title": "Go"}))

    assert env.turns.calls == [("enter", PROJECT, "live"), ("action", SESSION_ID, "go")]
    assert env.api.sent == []
    assert env.api.interactive == [("button", LINKED_NUMBER, "You picked go.", [("stay", "Stay")])]


async def test_a_list_row_is_a_choice_too_and_an_unsupported_interactive_reply_is_ignored(env: Env):
    env.turns.action_reply_message = "You picked the list option."
    await env.arrives(_interactive_payload(kind="list_reply", reply={"id": "opt2", "title": "Option 2"}))
    assert env.turns.calls == [("enter", PROJECT, "live"), ("action", SESSION_ID, "opt2")]
    assert env.api.sent == [(LINKED_NUMBER, "You picked the list option.")]

    env = Env()
    await env.arrives(_interactive_payload(kind="nfm_reply", reply={"response_json": "{}"}))
    assert env.turns.calls == []
    assert env.api.sent == [(LINKED_NUMBER, notices.UNSUPPORTED)]


async def test_a_transition_with_nothing_to_say_still_leaves_the_conversation_with_its_choices(env: Env):
    """The exchange produced no message to carry them, so they come on a
    prompt of their own rather than not at all."""
    env.turns.buttons = [_action("go", "Go")]

    await env.arrives(_interactive_payload())

    assert env.api.sent == []
    assert env.api.interactive == [("button", LINKED_NUMBER, notices.OPTIONS_PROMPT, [("go", "Go")])]


@pytest.mark.parametrize(("code", "text"), [
    ("session_channel_mismatch", notices.SESSION_TAKEN_OVER),
    ("session_superseded", notices.SESSION_TAKEN_OVER),
    ("session_not_found", notices.TECHNICAL_PROBLEM),
    ("session_closed", notices.TECHNICAL_PROBLEM),
])
async def test_a_refused_turn_becomes_this_channels_own_sentence_for_that_code(code, text):
    env = Env()
    env.turns.turn_error = _conflict(code)

    await env.arrives(_payload(text="hola"))

    assert env.api.sent == [(LINKED_NUMBER, text)]


async def test_a_state_that_takes_no_messages_says_so_and_offers_what_it_does_take():
    env = Env()
    env.turns.turn_error = _conflict("state_not_chat", "This state doesn't accept messages.")
    env.turns.buttons = [_action("go", "Go")]

    await env.arrives(_payload(text="hola"))

    assert env.api.sent == []
    assert env.api.interactive == [("button", LINKED_NUMBER, notices.NO_CHAT_STATE, [("go", "Go")])]


async def test_a_stale_choice_gets_a_notice_with_the_choices_that_are_current(env: Env):
    env.turns.action_error = ValueError("Action 'go' not available in state 'x'")
    env.turns.buttons = [_action("stay", "Stay")]

    await env.arrives(_interactive_payload())

    assert env.api.sent == []
    assert env.api.interactive == [("button", LINKED_NUMBER, notices.INVALID_ACTION, [("stay", "Stay")])]


async def test_a_choice_taken_while_a_reply_is_being_written_is_asked_to_wait(env: Env):
    env.turns.action_error = _conflict("turn_in_progress", "A chat reply is already being generated.")
    env.turns.buttons = [_action("stay", "Stay")]

    await env.arrives(_interactive_payload())

    assert env.api.sent == [(LINKED_NUMBER, notices.BUSY)]
    assert env.api.interactive == []


async def test_task_whatsapp_sends_to_a_linked_number_and_refuses_an_unknown_one(env: Env):
    assert await env.service.send_message(f"+{LINKED_NUMBER}", "**Hola**", PROJECT) is True
    assert env.api.sent == [(LINKED_NUMBER, "*Hola*")]

    assert await env.service.send_message(UNKNOWN_NUMBER, "hi", PROJECT) is False
    assert len(env.api.sent) == 1
