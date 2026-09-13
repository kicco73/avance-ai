from __future__ import annotations

import asyncio
import json
from http import HTTPStatus

import httpx
import pytest

from whatsapp.markdown import to_whatsapp_markdown
from whatsapp.webhook import (
    ButtonPress, IncomingMessage, SeenMessages, TextMessage, VoiceNote, extract_incoming,
)
from whatsapp.tests.whatsapp_helpers import (  # noqa: F401 — env is a fixture
    Env, _interactive_payload, _payload, _post, env,
)

REACHES_INTO = {
    "_client": "the httpx transport seam, the public surface being the Cloud API itself",
}

pytestmark = pytest.mark.contract


def _cloud_api_client(handler):
    from whatsapp.cloud_api_client import WhatsAppCloudApiClient

    api_client = WhatsAppCloudApiClient("tok", "123", "v23.0")
    api_client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://graph.facebook.com/v23.0",
    )
    return api_client


def test_the_verification_handshake_echoes_the_challenge_and_rejects_a_wrong_token(env: Env):
    client = env.client()
    ok = client.get("/api/skills/whatsapp/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "my-verify-token", "hub.challenge": "42",
    })
    assert ok.status_code == HTTPStatus.OK and ok.text == "42"

    wrong = client.get("/api/skills/whatsapp/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42",
    })
    assert wrong.status_code == HTTPStatus.FORBIDDEN


def test_an_unsigned_post_never_reaches_the_channel(env: Env):
    received: list = []
    env.service.receive = lambda incoming: received.append(incoming)

    refused = _post(env.client(), _payload(), signature="sha256=deadbeef")

    assert refused.status_code == HTTPStatus.FORBIDDEN
    assert received == []


def test_a_redelivery_is_handed_over_once(env: Env):
    received: list = []

    async def remember(incoming):
        received.append(incoming.id)

    env.service.receive = remember
    client = env.client()

    _post(client, _payload(msg_id="wamid.dup"))
    _post(client, _payload(msg_id="wamid.dup"))
    _post(client, _payload(msg_id="wamid.other"))

    assert received == ["wamid.dup", "wamid.other"]


def test_seen_messages_forgets_what_is_older_than_its_window():
    seen = SeenMessages(ttl_seconds=0)
    assert seen.check_and_add("wamid.1") is True
    assert seen.check_and_add("wamid.1") is True

    kept = SeenMessages()
    assert kept.check_and_add("wamid.1") is True
    assert kept.check_and_add("wamid.1") is False


def test_status_updates_are_not_messages():
    statuses = {"entry": [{"changes": [{"value": {
        "messaging_product": "whatsapp", "statuses": [{"id": "wamid.x", "status": "delivered"}],
    }}]}]}
    assert extract_incoming(statuses) == []


def test_each_kind_of_message_arrives_as_the_thing_it_is():
    [typed] = extract_incoming(_payload(text="hola"))
    assert isinstance(typed, TextMessage) and typed.text == "hola"

    [note] = extract_incoming(_payload(mtype="audio"))
    assert isinstance(note, VoiceNote) and note.audio_id == "media-in-1"

    [tapped] = extract_incoming(_interactive_payload())
    assert isinstance(tapped, ButtonPress) and tapped.action_id == "go"

    [listed] = extract_incoming(_interactive_payload(kind="list_reply", reply={"id": "opt2", "title": "Two"}))
    assert isinstance(listed, ButtonPress) and listed.action_id == "opt2"

    [unreadable] = extract_incoming(_payload(mtype="image"))
    assert type(unreadable) is IncomingMessage

    [blank] = extract_incoming(_payload(text="   "))
    assert type(blank) is IncomingMessage

    [unknown_reply] = extract_incoming(_interactive_payload(kind="nfm_reply", reply={"response_json": "{}"}))
    assert type(unknown_reply) is IncomingMessage


def test_an_invite_code_is_the_last_word_of_a_typed_message_and_nothing_else():
    [typed] = extract_incoming(_payload(text="Invitation code: GOODCODE"))
    assert typed.invite_code() == "GOODCODE"

    [note] = extract_incoming(_payload(mtype="audio"))
    assert note.invite_code() is None


def test_the_read_receipt_carries_the_typing_indicator_and_a_failure_is_swallowed():
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


def test_markdown_is_flattened_to_what_whatsapp_renders():
    src = "## Título\n\nHola **fuerte** y __otro__, mira [esto](https://x.y).\n\n* uno\n* dos\n- tres"
    assert to_whatsapp_markdown(src) == (
        "*Título*\n\nHola *fuerte* y *otro*, mira esto (https://x.y).\n\n- uno\n- dos\n- tres"
    )


def test_a_body_over_the_api_limit_is_split_on_a_boundary():
    from whatsapp.cloud_api_client import split_text

    text = ("palabra " * 1000).strip()
    chunks = split_text(text, 4096)
    assert len(chunks) == 2 and all(len(c) <= 4096 for c in chunks)
    assert " ".join(chunks) == text
