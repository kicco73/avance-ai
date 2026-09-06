from __future__ import annotations

import io

import pytest

from talk.talk_format import PcmWavCodec
from whatsapp.audio import WHATSAPP_AUDIO_MIME, Mp3Encoder, split_wav, wav_to_mp3
from whatsapp.cloud_api_client import split_text
from whatsapp.whatsapp_service import (
    REPLY_AUDIO_NOT_UNDERSTOOD, REPLY_NOT_LINKED, REPLY_OPTIONS_PROMPT, REPLY_PAUSED, REPLY_UNSUPPORTED_AUDIO,
    to_whatsapp_markdown,
)
from whatsapp_helpers import (  # noqa: F401 — env/voice_env are fixtures
    LINKED_EMAIL, LINKED_NUMBER, _FakeListen, _FakeTalk, _action, _build, _config, _payload, _post, _wav, env, voice_env,
)

pytestmark = pytest.mark.contract

TEXT_REPLY = "*Hola* — has dicho: hola"
VOICE_TEXT_REPLY = "*Hola* — has dicho: hola por voz"


def _voice():
    talk, listen = _FakeTalk(), _FakeListen()
    client, service, chat, db, api = _build(talk=talk, listen=listen)
    talk.chat = chat
    return client, service, chat, db, api, talk, listen


def _spoken_voice_note(**chat_overrides):
    client, _, chat, _, api, talk, listen = _voice()
    chat.reply_audio_text = "Hola."
    for name, value in chat_overrides.items():
        setattr(chat, name, value)
    return client, chat, api, talk, listen


# --- voice in ------------------------------------------------------------- #

def test_voice_note_is_transcribed_and_processed_as_text(voice_env):
    client, _, chat, db, api, talk, listen = voice_env
    _post(client, _payload(mtype="audio"))
    assert listen.heard == [b"OggS-fake-opus"]
    assert chat.calls == [("session", LINKED_EMAIL), ("turn", LINKED_EMAIL)]
    assert [m["content"] for m in db.messages if m["role"] == "user"] == ["hola por voz"]


def test_a_voice_note_that_cannot_be_transcribed_gets_a_notice_and_no_turn(env):
    client, _, chat, _, api = env
    _post(client, _payload(mtype="audio"))
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_UNSUPPORTED_AUDIO)]

    client, _, chat, _, api, _, listen = _voice()
    listen.transcript = "   "
    _post(client, _payload(mtype="audio"))
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_AUDIO_NOT_UNDERSTOOD)]

    client, _, chat, _, api, _, listen = _voice()
    listen.fail = True
    _post(client, _payload(mtype="audio"))
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_AUDIO_NOT_UNDERSTOOD)]

    client, _, chat, _, api, _, _ = _voice()
    api.media.clear()
    _post(client, _payload(mtype="audio"))
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_AUDIO_NOT_UNDERSTOOD)]

    client, _, chat, _, api, _, listen = _voice()
    _post(client, _payload(sender="34699999999", mtype="audio"))
    assert listen.heard == [] and chat.calls == []
    assert api.sent == [("34699999999", REPLY_NOT_LINKED)]


# --- voice out ------------------------------------------------------------ #

def test_the_default_policy_answers_in_kind_voice_for_voice_and_text_for_text():
    client, chat, api, talk, _ = _spoken_voice_note(reply_audio_text="Hola, te he oído.")
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == ["Hola, te he oído."]
    (mp3, mime), = api.uploaded
    assert mime == WHATSAPP_AUDIO_MIME and mp3[:3] == b"ID3"
    assert api.audio_sent == [(LINKED_NUMBER, "media-1")]
    assert api.sent == []

    client, chat, api, talk, _ = _spoken_voice_note()
    _post(client, _payload(text="hola"))
    assert talk.spoken == [] and api.audio_sent == []
    assert api.sent == [(LINKED_NUMBER, TEXT_REPLY)]


def test_the_always_policy_speaks_text_replies_and_the_never_policy_stays_text():
    talk = _FakeTalk()
    client, _, chat, _, api = _build(config=_config(voice_replies="always"), talk=talk)
    chat.reply_audio_text = "Hola."
    _post(client, _payload(text="hola"))
    assert talk.spoken == ["Hola."] and len(api.audio_sent) == 1 and api.sent == []

    talk, listen = _FakeTalk(), _FakeListen()
    client, _, chat, _, api = _build(config=_config(voice_replies="never"), talk=talk, listen=listen)
    chat.reply_audio_text = "Hola."
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == [] and api.audio_sent == []
    assert api.sent == [(LINKED_NUMBER, VOICE_TEXT_REPLY)]


def test_synthesis_starts_mid_turn_when_announced_otherwise_from_the_persisted_text_and_afresh_when_it_differs():
    """The reply's [audio] text is the first thing the model emits; the
    voice note's synthesis starts right then. The prefetch is an
    optimisation, not a dependency: with no audio metadata during the turn
    the note is synthesized on the spot from the persisted audio text, and
    when a regenerated reply persists a different text the note follows
    the persisted one."""
    client, _, api, talk, _ = _spoken_voice_note()
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == ["Hola."]
    assert talk.requested_during_turn == [True]
    assert api.audio_sent == [(LINKED_NUMBER, "media-1")]

    client, _, api, talk, _ = _spoken_voice_note(announces_audio=False)
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == ["Hola."]
    assert talk.requested_during_turn == [False]
    assert api.audio_sent == [(LINKED_NUMBER, "media-1")]

    client, _, api, talk, _ = _spoken_voice_note(announced_audio_text="Hola, primer intento.")
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == ["Hola, primer intento.", "Hola."]
    assert api.audio_sent == [(LINKED_NUMBER, "media-1")]


def test_every_voice_note_failure_falls_back_to_the_text_reply(monkeypatch):
    """The encoder goes through PyAV, whose own exception types don't
    derive from ValueError/httpx.HTTPError/ImportError — this used to
    escape _try_voice_note uncaught and leave the user with no reply at
    all instead of the text fallback."""
    client, _, api, talk, _ = _spoken_voice_note(reply_audio_text=None)
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == [] and api.audio_sent == []
    assert api.sent == [(LINKED_NUMBER, VOICE_TEXT_REPLY)]

    client, _, chat, _, api = _build(listen=_FakeListen())
    chat.reply_audio_text = "Hola."
    _post(client, _payload(mtype="audio"))
    assert api.audio_sent == []
    assert api.sent == [(LINKED_NUMBER, VOICE_TEXT_REPLY)]

    client, _, api, talk, _ = _spoken_voice_note()
    api.fail_upload = True
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == ["Hola."] and api.audio_sent == []
    assert api.sent == [(LINKED_NUMBER, VOICE_TEXT_REPLY)]

    client, _, api, talk, _ = _spoken_voice_note()
    talk.silent = True
    _post(client, _payload(mtype="audio"))
    assert api.uploaded == [] and api.sent == [(LINKED_NUMBER, VOICE_TEXT_REPLY)]

    def _boom(self, wav):
        raise RuntimeError("pyav exploded")

    monkeypatch.setattr("whatsapp.audio.Mp3Encoder.push", _boom)
    client, _, api, talk, _ = _spoken_voice_note()
    _post(client, _payload(mtype="audio"))
    assert api.audio_sent == [] and api.uploaded == []
    assert api.sent == [(LINKED_NUMBER, VOICE_TEXT_REPLY)]


def test_notices_are_never_spoken(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.session_payload = {"paused": True, "paused_reason": "quota"}
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == [] and api.sent == [(LINKED_NUMBER, REPLY_PAUSED)]


def test_manual_actions_follow_a_spoken_reply_as_buttons_and_stay_on_the_text_fallback():
    client, chat, api, _, _ = _spoken_voice_note()
    chat.state = {**chat.state, "manual_actions": [_action("go", "Go"), _action("stop", "Stop")]}
    _post(client, _payload(mtype="audio"))
    assert api.timeline == ["typing", "audio", "buttons"]
    kind, to, body, buttons = api.interactive[0]
    assert body == REPLY_OPTIONS_PROMPT and [b[0] for b in buttons] == ["go", "stop"]
    assert api.sent == []

    client, chat, api, _, _ = _spoken_voice_note()
    chat.state = {**chat.state, "manual_actions": [_action("go", "Go")]}
    api.fail_upload = True
    _post(client, _payload(mtype="audio"))
    assert api.timeline == ["typing", "buttons"]
    assert api.interactive[0][2] == VOICE_TEXT_REPLY


# --- audio encoding ------------------------------------------------------- #

def test_split_wav_handles_streaming_header_and_complete_file():
    pcm, rate = split_wav(_wav(rate=24000))
    assert rate == 24000 and len(pcm) == 12000 * 2
    streamed = PcmWavCodec.streaming_header(24000) + pcm
    assert split_wav(streamed) == (pcm, 24000)


def test_wav_to_mp3_produces_mono_48k_mp3_and_rejects_empty_audio():
    import av

    mp3 = wav_to_mp3(_wav(seconds=1.0))
    assert mp3[:3] == b"ID3"
    container = av.open(io.BytesIO(mp3))
    try:
        stream = container.streams.audio[0]
        assert stream.codec_context.name.startswith("mp3")
        assert stream.rate == 48000 and stream.layout.name == "mono"
    finally:
        container.close()
    assert len(mp3) < len(_wav(seconds=1.0)) // 3

    with pytest.raises(ValueError):
        wav_to_mp3(PcmWavCodec.streaming_header(22050))


def test_incremental_encoder_matches_whole_file_encoding_and_finishes_empty_with_no_audio():
    """Pushing the stream in arbitrary pieces — the header split too —
    must decode to the same audio as encoding the complete WAV at once."""
    import av

    def decoded_samples(mp3: bytes) -> int:
        container = av.open(io.BytesIO(mp3))
        try:
            return sum(frame.samples for frame in container.decode(audio=0))
        finally:
            container.close()

    wav = _wav(seconds=1.0, rate=24000)
    encoder = Mp3Encoder()
    for i in range(0, len(wav), 7):
        encoder.push(wav[i:i + 7])
    streamed = encoder.finish()
    assert streamed[:3] == b"ID3"
    assert decoded_samples(streamed) == decoded_samples(wav_to_mp3(wav))

    empty = Mp3Encoder()
    empty.push(PcmWavCodec.streaming_header(22050))
    assert empty.finish() == b""


# --- helpers -------------------------------------------------------------- #

def test_markdown_flattening_and_text_splitting_lose_nothing():
    src = "## Título\n\nHola **fuerte** y __otro__, mira [esto](https://x.y).\n\n* uno\n* dos\n- tres"
    assert to_whatsapp_markdown(src) == "*Título*\n\nHola *fuerte* y *otro*, mira esto (https://x.y).\n\n- uno\n- dos\n- tres"

    text = ("palabra " * 1000).strip()
    chunks = split_text(text, 4096)
    assert len(chunks) == 2 and all(len(c) <= 4096 for c in chunks)
    assert " ".join(chunks) == text
