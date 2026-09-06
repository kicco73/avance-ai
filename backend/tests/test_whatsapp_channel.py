from __future__ import annotations

import asyncio
import json
from http import HTTPStatus

import httpx
import pytest

from service_error import ServiceError
from session import Session
from whatsapp.whatsapp_service import (
    REPLY_ACCEPT_TERMS_LABEL, REPLY_BUSY, REPLY_DONE, REPLY_INVALID_ACTION, REPLY_NO_CHAT_STATE, REPLY_NOT_LINKED,
    REPLY_NOT_REGISTERED, REPLY_PAUSED, REPLY_REGISTERED, REPLY_SESSION_TAKEN_OVER, REPLY_TECHNICAL_PROBLEM,
    REPLY_TERMS_ACCEPTED, REPLY_UNSUPPORTED, IncomingMessage, WhatsAppService,
)
from whatsapp_helpers import (  # noqa: F401 — env is a fixture
    LINKED_EMAIL, LINKED_NUMBER, _action, _build, _config, _interactive_payload, _payload, _post, env,
)

pytestmark = pytest.mark.contract

GONE_SESSION_CODES = ["session_closed", "session_not_found"]
TAKEN_OVER_CODES = ["session_channel_mismatch", "session_superseded"]
REPLY_TEXT = "*Hola* — has dicho: hola"


def _conflict(code: str, message: str = "Session is not active.") -> ServiceError:
    return ServiceError(message, status_code=HTTPStatus.CONFLICT, code=code)


def _non_chat_state() -> ServiceError:
    return _conflict("state_not_chat", "This state doesn't accept messages; use an action instead.")


def _cloud_api_client(handler):
    from whatsapp.cloud_api_client import WhatsAppCloudApiClient

    api_client = WhatsAppCloudApiClient("tok", "123", "v23.0")
    api_client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://graph.facebook.com/v23.0")
    return api_client


# --- webhook plumbing ----------------------------------------------------- #

def test_verification_handshake_echoes_the_challenge_and_rejects_a_wrong_token(env):
    client, *_ = env
    ok = client.get("/api/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "my-verify-token", "hub.challenge": "42"})
    assert ok.status_code == HTTPStatus.OK and ok.text == "42"
    wrong = client.get("/api/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
    assert wrong.status_code == HTTPStatus.FORBIDDEN


def test_bad_signatures_status_updates_and_redeliveries_never_produce_a_second_turn(env):
    client, _, chat, _, api = env
    assert _post(client, _payload(), signature="sha256=deadbeef").status_code == HTTPStatus.FORBIDDEN
    assert chat.calls == [] and api.sent == []

    statuses = {"entry": [{"changes": [{"value": {"messaging_product": "whatsapp", "statuses": [{"id": "wamid.x", "status": "delivered"}]}}]}]}
    assert WhatsAppService.extract_incoming(statuses) == []

    _post(client, _payload(msg_id="wamid.dup"))
    _post(client, _payload(msg_id="wamid.dup"))
    assert [c for c in chat.calls if c[0] == "turn"] == [("turn", LINKED_EMAIL)]


# --- identity gate -------------------------------------------------------- #

def test_unlinked_unregistered_or_non_text_senders_get_a_canned_reply_and_no_turn():
    client, _, chat, _, api = _build()
    _post(client, _payload(sender="34699999999", mtype="audio"))
    assert chat.calls == []
    assert api.sent == [("34699999999", REPLY_NOT_LINKED)]

    client, _, chat, _, api = _build()
    _post(client, _payload(sender="34699999999", text="NOTACODE"))
    assert chat.calls == []
    assert api.sent == [("34699999999", "This invite link is invalid.")]

    client, _, chat, db, api = _build()
    db.users[LINKED_NUMBER]["role"] = None
    _post(client, _payload())
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_NOT_REGISTERED)]

    client, _, chat, _, api = _build()
    _post(client, _payload(mtype="image"))
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_UNSUPPORTED)]


def test_a_valid_invite_code_plain_or_wame_prefixed_registers_welcomes_and_delivers_the_opening_message():
    client, service, chat, db, api = _build()
    service._auth_service.valid_codes["GOODCODE"] = "demo-project"
    chat.opening_message = "Bienvenida."
    r = _post(client, _payload(sender="34699999999", text="GOODCODE"))
    assert r.status_code == HTTPStatus.OK
    assert db.users["34699999999"]["role"] == "user"
    assert chat.calls == [("session", "34699999999")]
    assert [body for _, body in api.sent] == [REPLY_REGISTERED, "Bienvenida."]

    client, service, _, db, _ = _build()
    service._auth_service.valid_codes["GOODCODE"] = "demo-project"
    _post(client, _payload(sender="34699999999", text="Invitation code: GOODCODE"))
    assert db.users["34699999999"]["role"] == "user"


def test_unexpected_error_during_redeem_gets_an_apology_not_silence(env):
    client, service, chat, db, api = env
    service._auth_service.unexpected_error = RuntimeError("boom")
    r = _post(client, _payload(sender="34699999999", text="GOODCODE"))
    assert r.status_code == HTTPStatus.OK
    assert chat.calls == []
    assert api.sent == [("34699999999", REPLY_TECHNICAL_PROBLEM)]
    assert "34699999999" not in db.users


# --- turn orchestration --------------------------------------------------- #

def test_turn_runs_as_the_linked_account_after_a_typing_indicator_replying_only_with_the_new_assistant_message(env):
    """A normal WhatsApp turn is the user's own — no AI-initiated opening
    message ahead of it (unlike the invite welcome), and earlier history
    is never resent."""
    client, _, chat, db, api = env
    chat.opening_message = "Bienvenida."
    db.add(7, "assistant", "mensaje de ayer")

    assert _post(client, _payload(text="hola")).status_code == HTTPStatus.OK

    assert chat.calls == [("session", LINKED_EMAIL), ("turn", LINKED_EMAIL)]
    assert api.read == ["wamid.1"]
    assert api.sent == [(LINKED_NUMBER, REPLY_TEXT)]
    assert api.timeline == ["typing", "text"]


def test_no_typing_indicator_when_mark_read_is_off():
    """The Cloud API only accepts the indicator inside the read receipt,
    so switching mark-read off in .config.yml switches it off too."""
    client, _, _, _, api = _build(_config(mark_read=False))
    _post(client, _payload(text="hola"))
    assert api.read == []
    assert api.timeline == ["text"]


def test_cloud_api_client_sends_the_read_receipt_with_the_typing_indicator_and_swallows_a_failure():
    posted: list[dict] = []

    def ok(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True})

    asyncio.run(_cloud_api_client(ok).mark_read_and_show_typing("wamid.1"))
    assert posted == [{
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": "wamid.1",
        "typing_indicator": {"type": "text"},
    }]

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    asyncio.run(_cloud_api_client(failing).mark_read_and_show_typing("wamid.1"))


def test_a_non_chat_state_becomes_a_notice_after_the_states_own_wrap_up_and_with_its_own_buttons():
    """The notice says "use an action instead" — it had better come with
    actions to use, not leave the user stuck with no buttons at all."""
    client, _, chat, _, api = _build()
    chat.wrap_up_message = "Conversación finalizada."
    chat.turn_error = _non_chat_state()
    _post(client, _payload(text="hola"))
    assert [body for _, body in api.sent] == ["Conversación finalizada.", REPLY_NO_CHAT_STATE]

    client, _, chat, _, api = _build()
    chat.turn_error = _non_chat_state()
    chat.state["manual_actions"] = [_action("go", "Go")]
    _post(client, _payload())
    assert api.sent == []
    assert api.interactive == [("button", LINKED_NUMBER, REPLY_NO_CHAT_STATE, [("go", "Go")])]


# --- error codes -> replies ------------------------------------------------ #

def test_taken_over_codes_become_the_taken_over_notice_for_turns_and_actions():
    for code in TAKEN_OVER_CODES:
        client, _, chat, _, api = _build()
        chat.turn_error = _conflict(code)
        _post(client, _payload(text="hola"))
        assert api.sent == [(LINKED_NUMBER, REPLY_SESSION_TAKEN_OVER)], code

        client, _, chat, _, api = _build()
        chat.action_error = _conflict(code)
        _post(client, _interactive_payload())
        assert api.sent == [(LINKED_NUMBER, REPLY_SESSION_TAKEN_OVER)], code


def test_turn_in_progress_becomes_the_busy_notice_for_turns_and_actions_with_no_buttons():
    busy = _conflict("turn_in_progress", "A chat reply is already being generated.")

    client, _, chat, _, api = _build()
    chat.turn_error = busy
    _post(client, _payload(text="hola"))
    assert api.sent == [(LINKED_NUMBER, REPLY_BUSY)]

    client, _, chat, _, api = _build()
    chat.action_error = busy
    chat.state["manual_actions"] = [_action("stay", "Stay")]
    _post(client, _interactive_payload())
    assert api.sent == [(LINKED_NUMBER, REPLY_BUSY)]
    assert api.interactive == []


@pytest.mark.parametrize("code", GONE_SESSION_CODES)
def test_a_gone_session_is_retried_once_for_turns_and_actions_reporting_a_technical_problem_if_it_fails_again(code):
    client, _, chat, _, api = _build()
    chat.turn_error = _conflict(code, "Session is closed.")
    chat.turn_error_clears_after_raise = True
    _post(client, _payload(text="hola"))
    assert [call for call in chat.calls if call[0] == "session"] == [("session", LINKED_EMAIL), ("session", LINKED_EMAIL)]
    assert api.sent == [(LINKED_NUMBER, REPLY_TEXT)]

    client, _, chat, _, api = _build()
    chat.turn_error = _conflict(code, "Session is closed.")
    _post(client, _payload(text="hola"))
    assert api.sent == [(LINKED_NUMBER, REPLY_TECHNICAL_PROBLEM)]

    client, _, chat, _, api = _build()
    chat.action_error = _conflict(code, "Session is closed.")
    chat.action_error_clears_after_raise = True
    _post(client, _interactive_payload())
    assert [call for call in chat.calls if call[0] == "session"] == [("session", LINKED_EMAIL), ("session", LINKED_EMAIL)]
    assert api.sent == [(LINKED_NUMBER, chat.state["ui_label"])]

    client, _, chat, _, api = _build()
    chat.action_error = _conflict(code, "Session is closed.")
    _post(client, _interactive_payload())
    assert api.sent == [(LINKED_NUMBER, REPLY_TECHNICAL_PROBLEM)]


# --- manual actions as buttons/list ---------------------------------------- #

def test_manual_actions_become_buttons_excluding_triggered_ones_truncating_long_titles_and_plain_text_when_none():
    client, _, chat, _, api = _build()
    chat.state["actions"] = [_action("go", "Go"), _action("stay", "Stay")]
    chat.state["manual_actions"] = chat.state["actions"]
    _post(client, _payload(text="hola"))
    assert api.sent == []
    assert api.interactive == [("button", LINKED_NUMBER, REPLY_TEXT, [("go", "Go"), ("stay", "Stay")])]

    client, _, chat, _, api = _build()
    triggered = _action("auto", "Auto", has_trigger=True)
    manual = _action("go", "A very very long button label indeed")
    chat.state["actions"] = [triggered, manual]
    chat.state["manual_actions"] = [manual]
    _post(client, _payload(text="hola"))
    assert api.interactive[0][3] == [("go", "A very very long bu…")]
    assert len(api.interactive[0][3][0][1]) == 20

    client, _, chat, _, api = _build()
    chat.state["actions"] = [triggered]
    chat.state["manual_actions"] = []
    _post(client, _payload(text="hola"))
    assert api.sent == [(LINKED_NUMBER, REPLY_TEXT)]
    assert api.interactive == []


def test_five_manual_actions_send_a_list(env):
    client, _, chat, _, api = env
    actions = [_action(f"a{i}", f"Action {i}", ui_description=f"Does {i}") for i in range(5)]
    chat.state["actions"] = actions
    chat.state["manual_actions"] = actions
    _post(client, _payload(text="hola"))
    assert len(api.interactive) == 1
    kind, to, body, button_text, rows = api.interactive[0]
    assert kind == "list" and to == LINKED_NUMBER and button_text == "Options"
    assert len(rows) == 5
    assert rows[0] == ("a0", "Action 0", "Does 0")


def test_button_and_list_replies_apply_the_action_as_the_linked_account_while_an_unsupported_reply_is_ignored():
    payload = _interactive_payload(kind="nfm_reply", reply={"response_json": "{}"})
    [message] = WhatsAppService.extract_incoming(payload)
    assert message.type == "interactive" and message.action_id is None

    client, _, chat, _, api = _build()
    chat.state["manual_actions"] = [_action("stay", "Stay")]
    chat.action_reply_message = "You picked go."
    _post(client, _interactive_payload(kind="button_reply", reply={"id": "go", "title": "Go"}))
    assert chat.calls == [("session", LINKED_EMAIL), ("action", LINKED_EMAIL, "go")]
    assert api.sent == []
    assert api.interactive == [("button", LINKED_NUMBER, "You picked go.", [("stay", "Stay")])]

    client, _, chat, _, api = _build()
    chat.action_reply_message = "You picked the list option."
    _post(client, _interactive_payload(kind="list_reply", reply={"id": "opt2", "title": "Option 2"}))
    assert chat.calls == [("session", LINKED_EMAIL), ("action", LINKED_EMAIL, "opt2")]
    assert api.sent == [(LINKED_NUMBER, "You picked the list option.")]


def test_an_action_with_no_message_falls_back_to_the_states_ui_label_then_done_and_an_invalid_one_gets_a_notice_with_buttons():
    client, _, chat, _, api = _build()
    chat.state["ui_label"] = "State Y"
    _post(client, _interactive_payload())
    assert api.sent == [(LINKED_NUMBER, "State Y")]

    client, _, chat, _, api = _build()
    chat.state["ui_label"] = None
    _post(client, _interactive_payload())
    assert api.sent == [(LINKED_NUMBER, REPLY_DONE)]

    client, _, chat, _, api = _build()
    chat.action_error = ValueError("Action 'go' not available in state 'x'")
    chat.state["manual_actions"] = [_action("stay", "Stay")]
    _post(client, _interactive_payload())
    assert chat.calls == [("session", LINKED_EMAIL), ("action", LINKED_EMAIL, "go")]
    assert api.sent == []
    assert api.interactive == [("button", LINKED_NUMBER, REPLY_INVALID_ACTION, [("stay", "Stay")])]


def test_paused_project_gate_is_relayed(env):
    client, _, chat, _, api = env
    chat.session_payload = {"paused": True, "paused_reason": "quota"}
    _post(client, _payload())
    assert api.sent == [(LINKED_NUMBER, REPLY_PAUSED)]
    assert [c for c in chat.calls if c[0] == "turn"] == []


# --- legal terms ------------------------------------------------------------ #

def test_pending_terms_send_the_content_with_an_accept_button_instead_of_a_turn(env):
    client, _, chat, _, api = env
    chat.session_payload = {"legal_terms_pending": True, "project_id": "demo-project"}
    chat.terms_content = "## Terms\n\nBe nice."
    _post(client, _payload(text="hola"))
    assert [c for c in chat.calls if c[0] == "turn"] == []
    assert api.sent == []
    kind, to, body, buttons = api.interactive[0]
    assert kind == "button" and to == LINKED_NUMBER
    assert body == "*Terms*\n\nBe nice."
    assert buttons == [("__whatsapp_accept_terms__", REPLY_ACCEPT_TERMS_LABEL)]


def test_registration_with_pending_terms_sends_terms_instead_of_the_welcome(env):
    client, service, chat, db, api = env
    service._auth_service.valid_codes["GOODCODE"] = "demo-project"
    chat.session_payload = {"legal_terms_pending": True, "project_id": "demo-project"}
    _post(client, _payload(sender="34699999999", text="GOODCODE"))
    assert db.users["34699999999"]["role"] == "user"
    assert [body for _, body in api.sent] == [REPLY_REGISTERED]
    kind, to, body, buttons = api.interactive[0]
    assert to == "34699999999" and body == "Please accept to continue."
    assert buttons == [("__whatsapp_accept_terms__", REPLY_ACCEPT_TERMS_LABEL)]


def test_accepting_terms_bootstraps_with_the_opening_message_or_a_plain_confirmation_never_resending_history():
    accept = _interactive_payload(reply={"id": "__whatsapp_accept_terms__", "title": "Accept"})
    pending = {"legal_terms_pending": True, "project_id": "demo-project"}

    client, _, chat, _, api = _build()
    chat.session_payload = dict(pending)
    chat.resolved_session_payload = {"id": 7}
    chat.opening_message = "Bienvenida."
    _post(client, accept)
    assert chat.accepted_terms_for == ["demo-project"]
    assert api.sent == [(LINKED_NUMBER, "Bienvenida.")]

    client, _, chat, db, api = _build()
    db.add(7, "assistant", "mensaje de ayer")
    chat.session_payload = dict(pending)
    chat.resolved_session_payload = {"id": 7}
    _post(client, accept)
    assert api.sent == [(LINKED_NUMBER, REPLY_TERMS_ACCEPTED)]


async def test_impersonation_does_not_leak_past_the_turn(env):
    # Direct call (no TestClient thread hop) so this context is the one
    # the turn ran in: conftest's default user must be back afterwards.
    _, service, chat, _, _ = env
    await service.handle(IncomingMessage(id="wamid.9", sender=LINKED_NUMBER, type="text", text="hola"))
    assert chat.calls == [("session", LINKED_EMAIL), ("turn", LINKED_EMAIL)]
    assert Session().user == "user"
