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

# --- webhook plumbing ----------------------------------------------------- #

def test_verification_handshake_echoes_challenge(env):
    client, *_ = env
    r = client.get("/api/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "my-verify-token", "hub.challenge": "42"})
    assert r.status_code == HTTPStatus.OK and r.text == "42"


def test_verification_rejects_wrong_token(env):
    client, *_ = env
    r = client.get("/api/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
    assert r.status_code == HTTPStatus.FORBIDDEN


def test_bad_signature_never_reaches_a_turn(env):
    client, _, chat, _, api = env
    assert _post(client, _payload(), signature="sha256=deadbeef").status_code == HTTPStatus.FORBIDDEN
    assert chat.calls == [] and api.sent == []


def test_status_updates_are_ignored():
    payload = {"entry": [{"changes": [{"value": {"messaging_product": "whatsapp", "statuses": [{"id": "wamid.x", "status": "delivered"}]}}]}]}
    assert WhatsAppService.extract_incoming(payload) == []


def test_redelivery_is_deduplicated(env):
    client, _, chat, _, _ = env
    _post(client, _payload(msg_id="wamid.dup"))
    _post(client, _payload(msg_id="wamid.dup"))
    assert [c for c in chat.calls if c[0] == "turn"] == [("turn", LINKED_EMAIL)]


# --- identity gate -------------------------------------------------------- #

def test_unlinked_number_sending_non_text_gets_canned_reply(env):
    client, _, chat, _, api = env
    _post(client, _payload(sender="34699999999", mtype="audio"))
    assert chat.calls == []
    assert api.sent == [("34699999999", REPLY_NOT_LINKED)]


def test_unlinked_number_with_an_unknown_code_gets_the_same_message_as_the_web(env):
    client, _, chat, _, api = env
    _post(client, _payload(sender="34699999999", text="NOTACODE"))
    assert chat.calls == []
    assert api.sent == [("34699999999", "This invite link is invalid.")]


def test_unlinked_number_with_a_valid_code_registers_and_welcomes(env):
    client, service, chat, db, api = env
    service._auth_service.valid_codes["GOODCODE"] = "demo-project"
    r = _post(client, _payload(sender="34699999999", text="GOODCODE"))
    assert r.status_code == HTTPStatus.OK
    assert db.users["34699999999"]["role"] == "user"
    assert chat.calls == [("session", "34699999999")]
    assert api.sent == [("34699999999", REPLY_REGISTERED)]


def test_unlinked_number_with_the_prefixed_wame_text_still_registers(env):
    client, service, _, db, _ = env
    service._auth_service.valid_codes["GOODCODE"] = "demo-project"
    _post(client, _payload(sender="34699999999", text="Invitation code: GOODCODE"))
    assert db.users["34699999999"]["role"] == "user"


def test_registration_delivers_the_projects_opening_message_too(env):
    client, service, chat, _, api = env
    service._auth_service.valid_codes["GOODCODE"] = "demo-project"
    chat.opening_message = "Bienvenida."
    _post(client, _payload(sender="34699999999", text="GOODCODE"))
    assert [body for _, body in api.sent] == [REPLY_REGISTERED, "Bienvenida."]


def test_unexpected_error_during_redeem_gets_an_apology_not_silence(env):
    client, service, chat, db, api = env
    service._auth_service.unexpected_error = RuntimeError("boom")
    r = _post(client, _payload(sender="34699999999", text="GOODCODE"))
    assert r.status_code == HTTPStatus.OK
    assert chat.calls == []
    assert api.sent == [("34699999999", REPLY_TECHNICAL_PROBLEM)]
    assert "34699999999" not in db.users


def test_linked_but_unregistered_account_is_refused(env):
    client, _, chat, db, api = env
    db.users[LINKED_NUMBER]["role"] = None
    _post(client, _payload())
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_NOT_REGISTERED)]


def test_non_text_message_gets_courtesy_reply(env):
    client, _, chat, _, api = env
    _post(client, _payload(mtype="image"))
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_UNSUPPORTED)]


# --- turn orchestration --------------------------------------------------- #

def test_turn_runs_as_the_linked_account_and_replies_with_persisted_assistant_messages(env):
    client, _, chat, _, api = env
    assert _post(client, _payload(text="hola")).status_code == HTTPStatus.OK
    assert chat.calls == [("session", LINKED_EMAIL), ("turn", LINKED_EMAIL)]
    assert api.read == ["wamid.1"]
    # Markdown flattened to WhatsApp's own subset on the way out.
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola")]


def test_typing_indicator_goes_out_before_the_reply_is_ready(env):
    client, _, _, _, api = env
    _post(client, _payload(text="hola"))
    assert api.timeline == ["typing", "text"]


def test_no_typing_indicator_when_mark_read_is_off():
    """The Cloud API only accepts the indicator inside the read receipt,
    so switching mark-read off in .config.yml switches it off too."""
    client, _, _, _, api = _build(_config(mark_read=False))
    _post(client, _payload(text="hola"))
    assert api.read == []
    assert api.timeline == ["text"]


def test_cloud_api_client_sends_the_read_receipt_with_the_typing_indicator():
    from whatsapp.cloud_api_client import WhatsAppCloudApiClient

    posted: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True})

    api_client = WhatsAppCloudApiClient("tok", "123", "v23.0")
    api_client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://graph.facebook.com/v23.0")
    asyncio.run(api_client.mark_read_and_show_typing("wamid.1"))
    assert posted == [{
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": "wamid.1",
        "typing_indicator": {"type": "text"},
    }]


def test_cloud_api_client_typing_indicator_failure_is_swallowed():
    from whatsapp.cloud_api_client import WhatsAppCloudApiClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    api_client = WhatsAppCloudApiClient("tok", "123", "v23.0")
    api_client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://graph.facebook.com/v23.0")
    asyncio.run(api_client.mark_read_and_show_typing("wamid.1"))


def test_no_ai_initiated_opening_message_ahead_of_the_users_own_turn(env):
    """Unlike the invite welcome (test_registration_delivers_the_projects_
    opening_message_too), a normal WhatsApp turn is the user's own —
    prepare_user_initiated_turn never announces the state on its own."""
    client, _, chat, _, api = env
    chat.opening_message = "Bienvenida."
    _post(client, _payload(text="hola"))
    assert [body for _, body in api.sent] == ["*Hola* — has dicho: hola"]


def test_a_chat_blocked_states_own_wrap_up_message_still_goes_out(env):
    client, _, chat, _, api = env
    chat.wrap_up_message = "Conversación finalizada."
    chat.turn_error = ServiceError("This state doesn't accept messages; use an action instead.", status_code=HTTPStatus.CONFLICT, code="state_not_chat")
    _post(client, _payload(text="hola"))
    assert [body for _, body in api.sent] == ["Conversación finalizada.", REPLY_NO_CHAT_STATE]


def test_earlier_history_is_not_resent(env):
    client, _, chat, db, api = env
    db.add(7, "assistant", "mensaje de ayer")
    _post(client, _payload(text="hola"))
    assert [body for _, body in api.sent] == ["*Hola* — has dicho: hola"]


def test_non_chat_state_conflict_becomes_a_notice(env):
    client, _, chat, _, api = env
    chat.turn_error = ServiceError("This state doesn't accept messages; use an action instead.", status_code=HTTPStatus.CONFLICT, code="state_not_chat")
    _post(client, _payload())
    assert api.sent == [(LINKED_NUMBER, REPLY_NO_CHAT_STATE)]


def test_non_chat_state_conflict_still_sends_the_states_own_buttons(env):
    """The notice says "use an action instead" — it had better come with
    actions to use, not leave the user stuck with no buttons at all."""
    client, _, chat, _, api = env
    chat.turn_error = ServiceError("This state doesn't accept messages; use an action instead.", status_code=HTTPStatus.CONFLICT, code="state_not_chat")
    chat.state["manual_actions"] = [_action("go", "Go")]
    _post(client, _payload())
    assert api.sent == []
    assert api.interactive == [("button", LINKED_NUMBER, REPLY_NO_CHAT_STATE, [("go", "Go")])]


# --- error codes -> replies (phase 4 point 6) ------------------------------- #

@pytest.mark.parametrize("code", ["session_channel_mismatch", "session_superseded"])
def test_turn_taken_over_codes_become_the_taken_over_notice(env, code):
    client, _, chat, _, api = env
    chat.turn_error = ServiceError("Session is not active.", status_code=HTTPStatus.CONFLICT, code=code)
    _post(client, _payload(text="hola"))
    assert api.sent == [(LINKED_NUMBER, REPLY_SESSION_TAKEN_OVER)]


@pytest.mark.parametrize("code", ["session_channel_mismatch", "session_superseded"])
def test_action_taken_over_codes_become_the_taken_over_notice(env, code):
    client, _, chat, _, api = env
    chat.action_error = ServiceError("Session is not active.", status_code=HTTPStatus.CONFLICT, code=code)
    _post(client, _interactive_payload())
    assert api.sent == [(LINKED_NUMBER, REPLY_SESSION_TAKEN_OVER)]


def test_turn_in_progress_code_becomes_the_busy_notice(env):
    client, _, chat, _, api = env
    chat.turn_error = ServiceError("A chat reply is already being generated.", status_code=HTTPStatus.CONFLICT, code="turn_in_progress")
    _post(client, _payload(text="hola"))
    assert api.sent == [(LINKED_NUMBER, REPLY_BUSY)]


@pytest.mark.parametrize("code", ["session_closed", "session_not_found"])
def test_turn_retries_once_on_a_gone_session_and_succeeds(env, code):
    client, _, chat, _, api = env
    chat.turn_error = ServiceError("Session is closed.", status_code=HTTPStatus.CONFLICT, code=code)
    chat.turn_error_clears_after_raise = True
    _post(client, _payload(text="hola"))
    assert [call for call in chat.calls if call[0] == "session"] == [("session", LINKED_EMAIL), ("session", LINKED_EMAIL)]
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola")]


@pytest.mark.parametrize("code", ["session_closed", "session_not_found"])
def test_turn_reports_a_technical_problem_when_the_retry_also_fails(env, code):
    client, _, chat, _, api = env
    chat.turn_error = ServiceError("Session is closed.", status_code=HTTPStatus.CONFLICT, code=code)
    _post(client, _payload(text="hola"))
    assert api.sent == [(LINKED_NUMBER, REPLY_TECHNICAL_PROBLEM)]


@pytest.mark.parametrize("code", ["session_closed", "session_not_found"])
def test_action_retries_once_on_a_gone_session_and_succeeds(env, code):
    client, _, chat, _, api = env
    chat.action_error = ServiceError("Session is closed.", status_code=HTTPStatus.CONFLICT, code=code)
    chat.action_error_clears_after_raise = True
    _post(client, _interactive_payload())
    assert [call for call in chat.calls if call[0] == "session"] == [("session", LINKED_EMAIL), ("session", LINKED_EMAIL)]
    assert api.sent == [(LINKED_NUMBER, chat.state["ui_label"])]


@pytest.mark.parametrize("code", ["session_closed", "session_not_found"])
def test_action_reports_a_technical_problem_when_the_retry_also_fails(env, code):
    client, _, chat, _, api = env
    chat.action_error = ServiceError("Session is closed.", status_code=HTTPStatus.CONFLICT, code=code)
    _post(client, _interactive_payload())
    assert api.sent == [(LINKED_NUMBER, REPLY_TECHNICAL_PROBLEM)]


# --- manual actions as buttons/list ---------------------------------------- #

def test_turn_landing_on_a_state_with_two_manual_actions_sends_buttons(env):
    client, _, chat, _, api = env
    chat.state["actions"] = [_action("go", "Go"), _action("stay", "Stay")]
    chat.state["manual_actions"] = chat.state["actions"]
    _post(client, _payload(text="hola"))
    assert api.sent == []
    assert api.interactive == [
        ("button", LINKED_NUMBER, "*Hola* — has dicho: hola", [("go", "Go"), ("stay", "Stay")])
    ]


def test_manual_actions_not_shown_are_excluded(env):
    client, _, chat, _, api = env
    triggered = _action("auto", "Auto", has_trigger=True)
    manual = _action("go", "Go")
    chat.state["actions"] = [triggered, manual]
    chat.state["manual_actions"] = [manual]
    _post(client, _payload(text="hola"))
    assert api.interactive[0][3] == [("go", "Go")]


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


def test_button_title_longer_than_20_chars_is_truncated(env):
    client, _, chat, _, api = env
    chat.state["actions"] = [_action("go", "A very very long button label indeed")]
    chat.state["manual_actions"] = chat.state["actions"]
    _post(client, _payload(text="hola"))
    _, _, _, buttons = api.interactive[0]
    assert buttons == [("go", "A very very long bu…")]
    assert len(buttons[0][1]) == 20


def test_state_with_no_manual_actions_sends_plain_text_only(env):
    client, _, chat, _, api = env
    chat.state["actions"] = [_action("auto", "Auto", has_trigger=True)]
    chat.state["manual_actions"] = []
    _post(client, _payload(text="hola"))
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola")]
    assert api.interactive == []


def test_extract_incoming_ignores_an_unsupported_interactive_reply():
    payload = _interactive_payload(kind="nfm_reply", reply={"response_json": "{}"})
    [message] = WhatsAppService.extract_incoming(payload)
    assert message.type == "interactive" and message.action_id is None


def test_button_reply_applies_the_action_as_the_linked_account(env):
    client, _, chat, _, api = env
    chat.state["manual_actions"] = [_action("stay", "Stay")]
    chat.action_reply_message = "You picked go."
    _post(client, _interactive_payload(kind="button_reply", reply={"id": "go", "title": "Go"}))
    assert chat.calls == [("session", LINKED_EMAIL), ("action", LINKED_EMAIL, "go")]
    assert api.sent == []
    assert api.interactive == [("button", LINKED_NUMBER, "You picked go.", [("stay", "Stay")])]


def test_list_reply_applies_the_action_too(env):
    client, _, chat, _, api = env
    chat.action_reply_message = "You picked the list option."
    _post(client, _interactive_payload(kind="list_reply", reply={"id": "opt2", "title": "Option 2"}))
    assert chat.calls == [("session", LINKED_EMAIL), ("action", LINKED_EMAIL, "opt2")]
    assert api.sent == [(LINKED_NUMBER, "You picked the list option.")]


def test_action_with_no_produced_message_falls_back_to_the_new_states_ui_label(env):
    client, _, chat, _, api = env
    chat.state["ui_label"] = "State Y"
    _post(client, _interactive_payload())
    assert api.sent == [(LINKED_NUMBER, "State Y")]


def test_action_with_no_message_and_no_ui_label_falls_back_to_done(env):
    client, _, chat, _, api = env
    chat.state["ui_label"] = None
    _post(client, _interactive_payload())
    assert api.sent == [(LINKED_NUMBER, REPLY_DONE)]


def test_invalid_action_gets_a_notice_and_the_current_states_buttons(env):
    client, _, chat, _, api = env
    chat.action_error = ValueError("Action 'go' not available in state 'x'")
    chat.state["manual_actions"] = [_action("stay", "Stay")]
    _post(client, _interactive_payload())
    assert chat.calls == [("session", LINKED_EMAIL), ("action", LINKED_EMAIL, "go")]
    assert api.sent == []
    assert api.interactive == [("button", LINKED_NUMBER, REPLY_INVALID_ACTION, [("stay", "Stay")])]


def test_action_conflict_gets_only_the_busy_notice(env):
    client, _, chat, _, api = env
    chat.action_error = ServiceError("A chat reply is already being generated.", status_code=HTTPStatus.CONFLICT, code="turn_in_progress")
    chat.state["manual_actions"] = [_action("stay", "Stay")]
    _post(client, _interactive_payload())
    assert api.sent == [(LINKED_NUMBER, REPLY_BUSY)]
    assert api.interactive == []


def test_paused_project_gate_is_relayed(env):
    client, _, chat, _, api = env
    chat.session_payload = {"paused": True, "paused_reason": "quota"}
    _post(client, _payload())
    assert api.sent == [(LINKED_NUMBER, REPLY_PAUSED)]
    assert [c for c in chat.calls if c[0] == "turn"] == []


# --- legal terms ------------------------------------------------------------ #

def test_pending_terms_send_the_content_with_an_accept_button(env):
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


def test_accepting_terms_calls_accept_legal_terms_and_bootstraps(env):
    client, _, chat, _, api = env
    chat.session_payload = {"legal_terms_pending": True, "project_id": "demo-project"}
    chat.resolved_session_payload = {"id": 7}
    chat.opening_message = "Bienvenida."
    _post(client, _interactive_payload(reply={"id": "__whatsapp_accept_terms__", "title": "Accept"}))
    assert chat.accepted_terms_for == ["demo-project"]
    assert api.sent == [(LINKED_NUMBER, "Bienvenida.")]


def test_accepting_terms_with_no_new_content_gets_a_plain_confirmation(env):
    client, _, chat, _, api = env
    chat.session_payload = {"legal_terms_pending": True, "project_id": "demo-project"}
    chat.resolved_session_payload = {"id": 7}
    _post(client, _interactive_payload(reply={"id": "__whatsapp_accept_terms__", "title": "Accept"}))
    assert api.sent == [(LINKED_NUMBER, REPLY_TERMS_ACCEPTED)]


def test_accepting_terms_does_not_resend_a_sessions_prior_history(env):
    client, _, chat, db, api = env
    db.add(7, "assistant", "mensaje de ayer")
    chat.session_payload = {"legal_terms_pending": True, "project_id": "demo-project"}
    chat.resolved_session_payload = {"id": 7}
    _post(client, _interactive_payload(reply={"id": "__whatsapp_accept_terms__", "title": "Accept"}))
    assert api.sent == [(LINKED_NUMBER, REPLY_TERMS_ACCEPTED)]


async def test_impersonation_does_not_leak_past_the_turn(env):
    # Direct call (no TestClient thread hop) so this context is the one
    # the turn ran in: conftest's default user must be back afterwards.
    _, service, chat, _, _ = env
    await service.handle(IncomingMessage(id="wamid.9", sender=LINKED_NUMBER, type="text", text="hola"))
    assert chat.calls == [("session", LINKED_EMAIL), ("turn", LINKED_EMAIL)]
    assert Session().user == "user"


# --- voice in ------------------------------------------------------------- #
