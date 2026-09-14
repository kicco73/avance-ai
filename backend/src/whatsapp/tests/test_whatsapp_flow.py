from __future__ import annotations

import pytest

from conftest import FakeAiService
from db.models import User
from system import bus
from system.bus import POINT_CORE_SERVICES
from whatsapp.audio import WHATSAPP_AUDIO_MIME
from whatsapp.tests.whatsapp_helpers import (
    LINKED_NUMBER, _FakeCloudApi, _FakeDecoder, _FakeSpeaker, _config, _payload, answered,
)
from whatsapp.webhook import extract_incoming
from whatsapp.whatsapp_service import WhatsAppService

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


class _Channel:
    """The channel as the skill builds it, against the real core — the
    same TurnService, Db and Bus a running process has."""

    def __init__(self, api: _FakeCloudApi, project_id: str) -> None:
        core = bus.collect(POINT_CORE_SERVICES, {})
        self.api = api
        self.db = core["db"]
        self.project_id = project_id
        self.service = WhatsAppService(
            _config(), core["turn_service"], core["db"], core["auth_service"], client=api,
        )
        self.service.listen([])

    async def arrives(self, payload: dict) -> None:
        for incoming in extract_incoming(payload):
            await answered(self.service.receive(incoming))

    def transcript(self, username: str = "user") -> list[tuple[str, str]]:
        session = self.db.get_latest_chat_session(username, self.project_id)
        assert session is not None
        return [(m["role"], m["content"]) for m in self.db.get_messages(session["id"])]

    def session(self, username: str = "user") -> dict:
        session = self.db.get_latest_chat_session(username, self.project_id)
        assert session is not None
        return session


@pytest.fixture
def whatsapp(client, hello_project) -> _Channel:
    db = client.app.state.db
    db.get_or_create_user("test", "sub-user", "user", "user", None)
    User.update(role="supervisor", whatsapp_phone_number=LINKED_NUMBER).where(User.id == "user").execute()
    return _Channel(_FakeCloudApi(), hello_project)


async def test_nothing_is_sent_until_the_person_writes(whatsapp: _Channel):
    """A conversation opened here says nothing on its own: what a browser
    is greeted with on entering (`session.opened`) has no answer on this
    channel. The first thing said is the answer to the first thing the
    person actually said."""
    assert whatsapp.api.sent == []

    await whatsapp.arrives(_payload(text="hola"))

    assert whatsapp.api.bodies == [REPLY_TEXT]
    assert {to for to, _ in whatsapp.api.sent} == {LINKED_NUMBER}
    assert whatsapp.session()["channel"] == "whatsapp"
    assert whatsapp.transcript() == [("user", "hola"), ("assistant", REPLY_TEXT)]


async def test_the_second_message_continues_the_same_conversation_and_is_answered_once(whatsapp: _Channel):
    await whatsapp.arrives(_payload(msg_id="wamid.1", text="hola"))
    first = whatsapp.session()["id"]

    await whatsapp.arrives(_payload(msg_id="wamid.2", text="otra vez"))

    assert whatsapp.session()["id"] == first
    assert whatsapp.api.bodies[-1:] == [REPLY_TEXT]
    assert whatsapp.transcript()[-2:] == [("user", "otra vez"), ("assistant", REPLY_TEXT)]


async def test_a_voice_note_is_decoded_runs_the_same_turn_and_comes_back_spoken(whatsapp: _Channel):
    decoder, speaker = _FakeDecoder(TRANSCRIPT), _FakeSpeaker()
    decoder.register()
    speaker.register()
    bus.contribute("turn.spoken_reply", lambda spoken: spoken.ask())

    await whatsapp.arrives(_payload(mtype="audio"))

    assert decoder.heard == [VOICE_NOTE]
    assert whatsapp.transcript() == [("user", TRANSCRIPT), ("assistant", REPLY_TEXT)]
    assert speaker.spoken == [SPOKEN_REPLY]
    assert [mime for _, mime in whatsapp.api.uploaded] == [WHATSAPP_AUDIO_MIME]
    assert whatsapp.api.audio_sent == [(LINKED_NUMBER, "media-1")]
    assert whatsapp.api.sent == []


async def test_a_whatsapp_native_account_gets_its_reply_once(client, hello_project):
    db = client.app.state.db
    db.get_or_create_user("whatsapp", None, None, None, None, LINKED_NUMBER)
    User.update(role="user", whatsapp_phone_number=LINKED_NUMBER).where(User.id == LINKED_NUMBER).execute()
    channel = _Channel(_FakeCloudApi(), hello_project)

    await channel.arrives(_payload(text="hola"))

    assert channel.api.bodies == [REPLY_TEXT]
    assert channel.transcript(LINKED_NUMBER) == [("user", "hola"), ("assistant", REPLY_TEXT)]
