from __future__ import annotations

import pytest

from conftest import (
    FakeAiService, chat_socket, chat_turn_frames, enter_chat, installed_skill, session_of,
    turn_frame_seconds, _frame_deadline,
)
from system import bus
from system.bus import INPUT_AUDIO, INPUT_TEXT, Message
from system.web_session import WebSession

REACHES_INTO = {
    "_PROVIDER_CLASSES": "the talk skill's provider registry, how a fake voice is installed",
    "_connections": "nothing tells a connection its own id, and input.audio is not CLIENT_INJECTABLE",
}

# This one module is a flow across three skills: what a browser says
# reaches a turn, and what the turn says back is spoken on talk's own
# route. Speaking is the real thing here, so the module collects only
# where that package is (see docs/TESTS.md); decoding speech is not —
# this channel only ever meets it as `input.text` coming back.
StreamingTalkProvider = pytest.importorskip("talk.talk_provider").StreamingTalkProvider
TalkService = pytest.importorskip("talk.talk_service").TalkService

pytestmark = pytest.mark.contract

REPLY_TEXT = "Fake AI reply."
SPOKEN_REPLY = "Fake AI reply, spoken."
TRANSCRIPT = "hola por voz"
VOICE_NOTE = b"OggS-fake-opus"


class _FakePiper(StreamingTalkProvider):

    def __init__(self, api_key=None, model=None) -> None:
        self.spoken: list[str] = []

    def _synthesize(self, text: str):
        self.spoken.append(text)
        yield b"\x00\x01" * 512, 22050


class _FakeDecoder:

    def __init__(self) -> None:
        self.heard: list[bytes] = []

    def register(self) -> None:
        bus.subscribe(INPUT_AUDIO, self._decode)

    async def _decode(self, message: Message) -> None:
        self.heard.append((message.body or {}).get("audio"))
        await bus.publish(message.converted(INPUT_TEXT, {"text": TRANSCRIPT}))


@pytest.fixture
def fake_ai_service() -> FakeAiService:
    service = FakeAiService()
    service.audio_text = SPOKEN_REPLY
    return service


@pytest.fixture
def raw_config() -> dict:
    return {
        "test-service": {"max-concurrent-tests": 1},
        "talk-service": {
            "enabled": True,
            "providers": [{"driver": "piper", "model": "fake-voice"}],
        },
    }


@pytest.fixture
def talk_provider(monkeypatch) -> _FakePiper:
    provider = _FakePiper()
    monkeypatch.setitem(TalkService._PROVIDER_CLASSES, "piper", lambda api_key, model: provider)
    return provider


@pytest.fixture
def webchat(talk_provider, client, hello_project):
    installed_skill("webchat")
    installed_skill("talk")
    session_id = session_of(enter_chat(client, hello_project))
    return client, session_id, talk_provider


def test_a_typed_message_runs_a_turn_and_its_reply_speaks_on_the_audio_route(webchat):
    client, session_id, talk = webchat

    frames = chat_turn_frames(client, session_id, "hola")

    assert [frame["type"] for frame in frames] == [
        "output.text_stream", "output.speech", "output.text_stream",
        "state.buttons", "output.text",
    ]
    assert [frame["text"] for frame in frames if frame["type"] == "output.speech"] == [SPOKEN_REPLY]
    assert [frame["text"] for frame in frames if frame["type"] == "output.text_stream" and frame["text"]] == [REPLY_TEXT]

    message_id = [frame for frame in frames if frame["type"] == "output.text"][-1]["assistant_message_id"]
    assert client.app.state.db.get_message_audio_text(message_id) == SPOKEN_REPLY
    audio = client.get(f"/api/skills/talk/messages/{message_id}/audio")
    assert audio.status_code == 200
    assert audio.content.startswith(b"RIFF")
    assert talk.spoken == [SPOKEN_REPLY]


def test_a_voice_note_on_an_open_connection_runs_the_very_same_turn(webchat):
    client, session_id, talk = webchat
    decoder = _FakeDecoder()
    decoder.register()

    frames: list[dict] = []
    with _frame_deadline(turn_frame_seconds(), frames):
        with chat_socket(client) as ws:
            connection_id = _own_connection_id(client)

            async def speak_into_the_socket() -> None:
                WebSession().user = "user"
                WebSession().role = "supervisor"
                await bus.publish(Message(
                    type=INPUT_AUDIO, body={"audio": VOICE_NOTE}, mime="audio/ogg", username="user",
                    session_id=session_id, origin_id=connection_id,
                ))

            ws.portal.call(speak_into_the_socket)
            while True:
                frame = ws.receive_json()
                frames.append(frame)
                if frame["type"] in ("output.text", "output.error") and "state.buttons" in [f["type"] for f in frames]:
                    break

    assert decoder.heard == [VOICE_NOTE]
    assert frames[-1]["type"] == "output.text"
    assert [frame["text"] for frame in frames if frame["type"] == "output.text_stream" and frame["text"]] == [REPLY_TEXT]
    assert talk.spoken == [SPOKEN_REPLY]
    messages = client.app.state.db.get_messages(session_id)
    assert [(m["role"], m["content"]) for m in messages][-2:] == [("user", TRANSCRIPT), ("assistant", REPLY_TEXT)]


def _own_connection_id(client) -> str:
    """The one reach-in this module keeps, and why: a voice note has to
    name the connection it arrived on, INPUT_AUDIO is not CLIENT_INJECTABLE
    so a browser cannot send one down this socket, and nothing tells a
    connection its own id (see system/bus_channel.py, has_connection)."""
    return next(
        connection.id
        for open_connections in client.app.state.bus_channel._connections.values()
        for connection in open_connections
    )
