from __future__ import annotations

import pytest

from conftest import FakeAiService, chat_socket, chat_turn_frames, installed_skill, turn_frame_seconds, _frame_deadline
from listen.decoder import SpeechDecoder
from system import bus
from system.bus import INPUT_AUDIO, Message
from system.session import Session
from talk.talk_provider import StreamingTalkProvider
from talk.talk_service import TalkService

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


class _FakeListen:

    def __init__(self) -> None:
        self.heard: list[bytes] = []

    async def transcribe(self, audio: bytes) -> str:
        self.heard.append(audio)
        return TRANSCRIPT


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
    response = client.get("/api/skills/webchat/sessions/current")
    assert response.status_code == 200, response.text
    return client, response.json()["id"], talk_provider


def test_a_typed_message_runs_a_turn_and_its_reply_speaks_on_the_audio_route(webchat):
    client, session_id, talk = webchat

    frames = chat_turn_frames(client, session_id, "hola")

    assert [frame["type"] for frame in frames] == [
        "turn.started", "output.speech", "output.text", "turn.ended",
    ]
    assert [frame["body"] for frame in frames if frame["type"] == "output.speech"] == [SPOKEN_REPLY]
    assert [frame["body"] for frame in frames if frame["type"] == "output.text"] == [REPLY_TEXT]

    message_id = frames[-1]["assistant_message_id"]
    assert client.app.state.db.get_message_audio_text(message_id) == SPOKEN_REPLY
    audio = client.get(f"/api/skills/talk/messages/{message_id}/audio")
    assert audio.status_code == 200
    assert audio.content.startswith(b"RIFF")
    assert talk.spoken == [SPOKEN_REPLY]


def test_a_voice_note_on_an_open_connection_runs_the_very_same_turn(webchat):
    client, session_id, talk = webchat
    listen = _FakeListen()
    SpeechDecoder(listen).register()

    frames: list[dict] = []
    with _frame_deadline(turn_frame_seconds(), frames):
        with chat_socket(client) as ws:
            connection_id = _only_connection(client).id

            async def speak_into_the_socket() -> None:
                Session().user = "user"
                Session().role = "supervisor"
                await bus.publish(Message(
                    type=INPUT_AUDIO, body=VOICE_NOTE, mime="audio/ogg", username="user",
                    session_id=session_id, origin_id=connection_id, stream_id="t1",
                ))

            ws.portal.call(speak_into_the_socket)
            while True:
                frame = ws.receive_json()
                frames.append(frame)
                if frame["type"] in ("turn.ended", "turn.failed"):
                    break

    assert listen.heard == [VOICE_NOTE]
    assert frames[-1]["type"] == "turn.ended"
    assert [frame["body"] for frame in frames if frame["type"] == "output.text"] == [REPLY_TEXT]
    assert talk.spoken == [SPOKEN_REPLY]
    messages = client.app.state.db.get_messages(session_id)
    assert [(m["role"], m["content"]) for m in messages][-2:] == [("user", TRANSCRIPT), ("assistant", REPLY_TEXT)]


def _only_connection(client):
    connections = [
        connection
        for open_connections in client.app.state.bus_channel._connections.values()
        for connection in open_connections
    ]
    assert len(connections) == 1, connections
    return connections[0]
