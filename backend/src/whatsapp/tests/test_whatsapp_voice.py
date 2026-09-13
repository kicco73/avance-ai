from __future__ import annotations

import io

import pytest

from system.audio_format import PcmWavCodec
from whatsapp import notices
from whatsapp.audio import WHATSAPP_AUDIO_MIME, Mp3Encoder, split_wav, wav_to_mp3
from whatsapp.tests.whatsapp_helpers import (  # noqa: F401 — voice_env is a fixture
    LINKED_NUMBER, PROJECT, SESSION_ID, UNKNOWN_NUMBER, Env, _FakeDecoder, _FakeSpeaker,
    _SpeechlessDecoder, _action, _config, _payload, _wav, voice_env,
)

pytestmark = pytest.mark.contract

TEXT_REPLY = "*Hola* — has dicho: hola"
VOICE_TEXT_REPLY = "*Hola* — has dicho: hola por voz"


def _speaking(**overrides) -> Env:
    env = Env(speaker=_FakeSpeaker(), decoder=_FakeDecoder())
    env.turns.reply_audio_text = "Hola."
    for name, value in overrides.items():
        setattr(env.turns, name, value)
    return env


# --- a voice note coming in -------------------------------------------------- #

async def test_a_voice_note_is_decoded_and_runs_the_very_same_turn(voice_env: Env):
    await voice_env.arrives(_payload(mtype="audio"))

    assert voice_env.decoder.heard == [b"OggS-fake-opus"]
    assert voice_env.turns.calls == [("enter", PROJECT, "live"), ("turn", SESSION_ID, "hola por voz")]
    assert [m["content"] for m in voice_env.db.messages if m["role"] == "user"] == ["hola por voz"]


async def test_a_build_that_cannot_listen_says_so_and_one_that_heard_nothing_says_something_else():
    env = Env()
    await env.arrives(_payload(mtype="audio"))
    assert env.turns.calls == [("enter", PROJECT, "live")]
    assert env.api.sent == [(LINKED_NUMBER, notices.UNSUPPORTED_AUDIO)]

    env = Env(speaker=_FakeSpeaker(), decoder=_SpeechlessDecoder())
    await env.arrives(_payload(mtype="audio"))
    assert env.api.sent == [(LINKED_NUMBER, notices.AUDIO_NOT_UNDERSTOOD)]


async def test_a_voice_note_that_cannot_be_fetched_is_a_notice_too_and_an_unlinked_one_is_never_fetched():
    env = _speaking()
    env.api.media.clear()
    await env.arrives(_payload(mtype="audio"))
    assert [call for call in env.turns.calls if call[0] == "turn"] == []
    assert env.api.sent == [(LINKED_NUMBER, notices.AUDIO_NOT_UNDERSTOOD)]

    env = _speaking()
    await env.arrives(_payload(sender=UNKNOWN_NUMBER, mtype="audio"))
    assert env.decoder.heard == [] and env.turns.calls == []
    assert env.api.sent == [(UNKNOWN_NUMBER, notices.NOT_LINKED)]


# --- a voice note going out --------------------------------------------------- #

async def test_the_default_policy_answers_in_kind():
    env = _speaking(reply_audio_text="Hola, te he oído.")
    await env.arrives(_payload(mtype="audio"))
    assert env.speaker.spoken == ["Hola, te he oído."]
    (mp3, mime), = env.api.uploaded
    assert mime == WHATSAPP_AUDIO_MIME and mp3[:3] == b"ID3"
    assert env.api.audio_sent == [(LINKED_NUMBER, "media-1")]
    assert env.api.sent == []

    env = _speaking()
    await env.arrives(_payload(text="hola"))
    assert env.speaker.spoken == [] and env.api.audio_sent == []
    assert env.api.sent == [(LINKED_NUMBER, TEXT_REPLY)]


async def test_always_speaks_a_typed_reply_too_and_never_keeps_every_reply_written():
    env = Env(config=_config(voice_replies="always"), speaker=_FakeSpeaker())
    env.turns.reply_audio_text = "Hola."
    await env.arrives(_payload(text="hola"))
    assert env.speaker.spoken == ["Hola."] and len(env.api.audio_sent) == 1 and env.api.sent == []

    env = Env(config=_config(voice_replies="never"), speaker=_FakeSpeaker(), decoder=_FakeDecoder())
    env.turns.reply_audio_text = "Hola."
    await env.arrives(_payload(mtype="audio"))
    assert env.speaker.spoken == [] and env.api.audio_sent == []
    assert env.api.sent == [(LINKED_NUMBER, VOICE_TEXT_REPLY)]


async def test_synthesis_starts_the_moment_the_turn_announces_the_spoken_text():
    env = _speaking()
    await env.arrives(_payload(mtype="audio"))
    assert env.speaker.spoken == ["Hola."]
    assert env.speaker.requested_during_turn == [True]
    assert env.api.audio_sent == [(LINKED_NUMBER, "media-1")]

    env = _speaking(announces_audio=False)
    await env.arrives(_payload(mtype="audio"))
    assert env.speaker.spoken == ["Hola."]
    assert env.speaker.requested_during_turn == [False]
    assert env.api.audio_sent == [(LINKED_NUMBER, "media-1")]

    env = _speaking(announced_audio_text="Hola, primer intento.")
    await env.arrives(_payload(mtype="audio"))
    assert env.speaker.spoken == ["Hola, primer intento.", "Hola."]
    assert env.api.audio_sent == [(LINKED_NUMBER, "media-1")]


async def test_every_way_a_voice_note_can_fail_falls_back_to_the_written_reply(monkeypatch):
    env = _speaking(reply_audio_text=None)
    await env.arrives(_payload(mtype="audio"))
    assert env.speaker.spoken == [] and env.api.audio_sent == []
    assert env.api.sent == [(LINKED_NUMBER, VOICE_TEXT_REPLY)]

    env = Env(decoder=_FakeDecoder())
    env.turns.reply_audio_text = "Hola."
    await env.arrives(_payload(mtype="audio"))
    assert env.api.audio_sent == []
    assert env.api.sent == [(LINKED_NUMBER, VOICE_TEXT_REPLY)]

    env = _speaking()
    env.api.fail_upload = True
    await env.arrives(_payload(mtype="audio"))
    assert env.speaker.spoken == ["Hola."] and env.api.audio_sent == []
    assert env.api.sent == [(LINKED_NUMBER, VOICE_TEXT_REPLY)]

    env = _speaking()
    env.speaker.silent = True
    await env.arrives(_payload(mtype="audio"))
    assert env.api.uploaded == [] and env.api.sent == [(LINKED_NUMBER, VOICE_TEXT_REPLY)]

    def _boom(self, wav):
        raise RuntimeError("pyav exploded")

    monkeypatch.setattr("whatsapp.audio.Mp3Encoder.push", _boom)
    env = _speaking()
    await env.arrives(_payload(mtype="audio"))
    assert env.api.audio_sent == [] and env.api.uploaded == []
    assert env.api.sent == [(LINKED_NUMBER, VOICE_TEXT_REPLY)]


async def test_a_notice_is_never_spoken(voice_env: Env):
    voice_env.turns.session = {"blocked": "paused", "detail": "quota"}

    await voice_env.arrives(_payload(mtype="audio"))

    assert voice_env.speaker.spoken == []
    assert voice_env.api.sent == [(LINKED_NUMBER, notices.PAUSED)]


async def test_the_choices_follow_a_spoken_reply_and_stay_on_the_written_fallback():
    env = _speaking()
    env.turns.buttons = [_action("go", "Go"), _action("stop", "Stop")]
    await env.arrives(_payload(mtype="audio"))
    assert env.api.timeline == ["typing", "audio", "buttons"]
    kind, to, body, buttons = env.api.interactive[0]
    assert body == notices.OPTIONS_PROMPT and [b[0] for b in buttons] == ["go", "stop"]
    assert env.api.sent == []

    env = _speaking()
    env.turns.buttons = [_action("go", "Go")]
    env.api.fail_upload = True
    await env.arrives(_payload(mtype="audio"))
    assert env.api.timeline == ["typing", "buttons"]
    assert env.api.interactive[0][2] == VOICE_TEXT_REPLY


# --- audio encoding ----------------------------------------------------------- #

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
