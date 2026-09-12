from __future__ import annotations

import pytest

from conftest import FakeAiService
from db.models import User
from listen.decoder import SpeechDecoder
from system import bus
from system.bus import OUTPUT_AUDIO_STREAM, OUTPUT_SPEECH, Message
from talk.audio_stream import AudioStream
from whatsapp import whatsapp_service as whatsapp_module
from whatsapp.audio import WHATSAPP_AUDIO_MIME
from whatsapp.tests.whatsapp_helpers import (
    APP_SECRET, LINKED_NUMBER, _FakeCloudApi, _FakeListen, _FakeTalk, _payload, _post,
)

pytestmark = pytest.mark.contract

REPLY_TEXT = "Fake AI reply."
SPOKEN_REPLY = "Fake AI reply, spoken."
TRANSCRIPT = "hola por voz"
VOICE_NOTE = b"OggS-fake-opus"


@pytest.fixture
def fake_ai_service() -> FakeAiService:
    service = FakeAiService()
    service.audio_text = SPOKEN_REPLY
    return service


@pytest.fixture
def raw_config() -> dict:
    return {
        "test-service": {"max-concurrent-tests": 1},
        "whatsapp-service": {
            "enabled": True,
            "verify-token": "my-verify-token",
            "app-secret": APP_SECRET,
            "access-token": "tok",
            "phone-number-id": "123",
            "voice-replies": "when-spoken-to",
        },
    }


@pytest.fixture
def cloud_api(monkeypatch) -> _FakeCloudApi:
    api = _FakeCloudApi()
    monkeypatch.setattr(whatsapp_module, "WhatsAppCloudApiClient", lambda *args, **kwargs: api)
    return api


@pytest.fixture
def whatsapp(cloud_api, client, hello_project):
    db = client.app.state.db
    db.get_or_create_user("test", "sub-user", "user", "user", None)
    User.update(role="supervisor", whatsapp_phone_number=LINKED_NUMBER).where(User.id == "user").execute()
    return client, cloud_api, db, hello_project


def _speaking(talk: _FakeTalk) -> None:
    async def speak(message: Message) -> None:
        await bus.publish(message.converted(
            OUTPUT_AUDIO_STREAM, {"stream": AudioStream(talk, str(message.body["text"]))}, mime="audio/wav",
        ))

    bus.subscribe(OUTPUT_SPEECH, speak)


def _session_of(db, project_id: str) -> dict:
    session = db.get_latest_chat_session("user", project_id)
    assert session is not None
    return session


def test_a_typed_message_runs_a_real_turn_and_the_reply_goes_back_as_text(whatsapp):
    client, api, db, project_id = whatsapp

    assert _post(client, _payload(text="hola")).status_code == 200

    assert [body for _, body in api.sent] == [REPLY_TEXT, REPLY_TEXT]
    assert {to for to, _ in api.sent} == {LINKED_NUMBER}
    session = _session_of(db, project_id)
    assert session["channel"] == "whatsapp"
    assert [(m["role"], m["content"]) for m in db.get_messages(session["id"])] == [
        ("assistant", REPLY_TEXT), ("user", "hola"), ("assistant", REPLY_TEXT),
    ]


def test_a_voice_note_is_decoded_runs_the_same_turn_and_comes_back_spoken(whatsapp):
    client, api, db, project_id = whatsapp
    listen, talk = _FakeListen(TRANSCRIPT), _FakeTalk()
    SpeechDecoder(listen).register()
    _speaking(talk)

    assert _post(client, _payload(mtype="audio")).status_code == 200

    assert listen.heard == [VOICE_NOTE]
    session = _session_of(db, project_id)
    assert [(m["role"], m["content"]) for m in db.get_messages(session["id"])] == [
        ("assistant", REPLY_TEXT), ("user", TRANSCRIPT), ("assistant", REPLY_TEXT),
    ]
    assert [m["audio_text"] for m in db.get_messages(session["id"]) if m["role"] == "assistant"] == [
        SPOKEN_REPLY, SPOKEN_REPLY,
    ]
    assert talk.spoken == [SPOKEN_REPLY, SPOKEN_REPLY]
    assert [mime for _, mime in api.uploaded] == [WHATSAPP_AUDIO_MIME, WHATSAPP_AUDIO_MIME]
    assert api.audio_sent == [(LINKED_NUMBER, "media-1"), (LINKED_NUMBER, "media-2")]
    assert api.sent == []
