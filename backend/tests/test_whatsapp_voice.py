from __future__ import annotations

import io

import pytest

from talk.talk_format import PcmWavCodec
from whatsapp.audio import WHATSAPP_AUDIO_MIME, split_wav, wav_to_mp3
from whatsapp.cloud_api_client import split_text
from whatsapp.whatsapp_service import (
    REPLY_AUDIO_NOT_UNDERSTOOD, REPLY_NOT_LINKED, REPLY_OPTIONS_PROMPT, REPLY_PAUSED, REPLY_UNSUPPORTED_AUDIO,
    to_whatsapp_markdown,
)
from whatsapp_helpers import (  # noqa: F401 — env/voice_env are fixtures
    LINKED_EMAIL, LINKED_NUMBER, _FakeListen, _FakeTalk, _action, _build, _config, _payload, _post, _wav, env, voice_env,
)

pytestmark = pytest.mark.contract

def test_voice_note_is_transcribed_and_processed_as_text(voice_env):
    client, _, chat, db, api, talk, listen = voice_env
    _post(client, _payload(mtype="audio"))
    assert listen.heard == [b"OggS-fake-opus"]
    assert chat.calls == [("session", LINKED_EMAIL), ("turn", LINKED_EMAIL)]
    # The transcript is what got persisted as the user's own message.
    assert [m["content"] for m in db.messages if m["role"] == "user"] == ["hola por voz"]


def test_voice_note_without_listen_service_gets_notice(env):
    client, _, chat, _, api = env
    _post(client, _payload(mtype="audio"))
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_UNSUPPORTED_AUDIO)]


def test_unintelligible_voice_note_gets_notice(voice_env):
    client, _, chat, _, api, _, listen = voice_env
    listen.transcript = "   "
    _post(client, _payload(mtype="audio"))
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_AUDIO_NOT_UNDERSTOOD)]


def test_transcription_failure_gets_notice_not_exception(voice_env):
    client, _, chat, _, api, _, listen = voice_env
    listen.fail = True
    _post(client, _payload(mtype="audio"))
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_AUDIO_NOT_UNDERSTOOD)]


def test_media_download_failure_gets_notice(voice_env):
    client, _, chat, _, api, _, _ = voice_env
    api.media.clear()
    _post(client, _payload(mtype="audio"))
    assert chat.calls == []
    assert api.sent == [(LINKED_NUMBER, REPLY_AUDIO_NOT_UNDERSTOOD)]


def test_voice_note_from_unlinked_number_is_not_transcribed(voice_env):
    client, _, chat, _, api, _, listen = voice_env
    _post(client, _payload(sender="34699999999", mtype="audio"))
    assert listen.heard == [] and chat.calls == []
    assert api.sent == [("34699999999", REPLY_NOT_LINKED)]


# --- voice out ------------------------------------------------------------ #

def test_voice_note_in_gets_voice_note_out(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola, te he oído."
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == ["Hola, te he oído."]
    (mp3, mime), = api.uploaded
    assert mime == WHATSAPP_AUDIO_MIME and mp3[:3] == b"ID3"
    assert api.audio_sent == [(LINKED_NUMBER, "media-1")]
    # Answer in kind: the voice note replaces the text, it doesn't duplicate it.
    assert api.sent == []


def test_text_in_gets_text_out_even_with_voice_available(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola."
    _post(client, _payload(text="hola"))
    assert talk.spoken == [] and api.audio_sent == []
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola")]


def test_voice_policy_always_speaks_text_replies_too():
    talk = _FakeTalk()
    client, _, chat, _, api = _build(config=_config(voice_replies="always"), talk=talk)
    chat.reply_audio_text = "Hola."
    _post(client, _payload(text="hola"))
    assert talk.spoken == ["Hola."] and len(api.audio_sent) == 1 and api.sent == []


def test_voice_policy_never_stays_text():
    talk, listen = _FakeTalk(), _FakeListen()
    client, _, chat, _, api = _build(config=_config(voice_replies="never"), talk=talk, listen=listen)
    chat.reply_audio_text = "Hola."
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == [] and api.audio_sent == []
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola por voz")]


def test_synthesis_starts_while_the_turn_is_still_running(voice_env):
    """The reply's [audio] text is the first thing the model emits; the
    voice note's synthesis starts right then, not once the whole turn
    (text, signals, env, persistence) is over — the send itself then
    finds that generation in flight or cached."""
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola."
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == ["Hola."]
    assert talk.requested_during_turn == [True]
    assert api.audio_sent == [(LINKED_NUMBER, "media-1")]


def test_a_turn_that_never_announced_its_audio_text_still_gets_a_voice_note(voice_env):
    """The prefetch is an optimisation, not a dependency: with no audio
    metadata during the turn (a fixed-message state, an older strategy),
    the note is synthesized on the spot from the persisted audio text."""
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola."
    chat.announces_audio = False
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == ["Hola."]
    assert talk.requested_during_turn == [False]
    assert api.audio_sent == [(LINKED_NUMBER, "media-1")]


def test_a_regenerated_reply_with_a_different_audio_text_is_synthesized_afresh(voice_env):
    """A state transition regenerates the reply, so the audio text the
    model first announced isn't the one persisted — the note follows the
    persisted one, and the early synthesis is simply left unused."""
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola."
    chat.announced_audio_text = "Hola, primer intento."
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == ["Hola, primer intento.", "Hola."]
    assert api.audio_sent == [(LINKED_NUMBER, "media-1")]


def test_no_synthesis_ahead_of_a_typed_message_under_the_default_policy(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola."
    _post(client, _payload(text="hola"))
    assert talk.spoken == []
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola")]


def test_reply_without_audio_text_falls_back_to_text(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = None
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == [] and api.audio_sent == []
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola por voz")]


def test_voice_note_without_talk_service_falls_back_to_text():
    client, _, chat, _, api = _build(listen=_FakeListen())
    chat.reply_audio_text = "Hola."
    _post(client, _payload(mtype="audio"))
    assert api.audio_sent == []
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola por voz")]


def test_encoding_error_of_any_kind_falls_back_to_text(voice_env, monkeypatch):
    """The encoder goes through PyAV, whose own exception types don't
    derive from ValueError/httpx.HTTPError/ImportError — this used to
    escape _try_voice_note uncaught and leave the user with no reply at
    all instead of the text fallback."""
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola."

    def _boom(self, wav):
        raise RuntimeError("pyav exploded")

    monkeypatch.setattr("whatsapp.audio.Mp3Encoder.push", _boom)
    _post(client, _payload(mtype="audio"))
    assert api.audio_sent == [] and api.uploaded == []
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola por voz")]


def test_upload_failure_falls_back_to_text(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola."
    api.fail_upload = True
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == ["Hola."] and api.audio_sent == []
    assert api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola por voz")]


def test_silent_talk_service_falls_back_to_text(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola."
    talk.silent = True
    _post(client, _payload(mtype="audio"))
    assert api.uploaded == [] and api.sent == [(LINKED_NUMBER, "*Hola* — has dicho: hola por voz")]


def test_notices_are_never_spoken(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.session_payload = {"paused": True, "paused_reason": "quota"}
    _post(client, _payload(mtype="audio"))
    assert talk.spoken == [] and api.sent == [(LINKED_NUMBER, REPLY_PAUSED)]


def test_spoken_reply_with_manual_actions_gets_buttons_on_a_follow_up(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola."
    chat.state = {**chat.state, "manual_actions": [_action("go", "Go"), _action("stop", "Stop")]}
    _post(client, _payload(mtype="audio"))
    assert api.timeline == ["typing", "audio", "buttons"]
    kind, to, body, buttons = api.interactive[0]
    assert body == REPLY_OPTIONS_PROMPT and [b[0] for b in buttons] == ["go", "stop"]
    assert api.sent == []


def test_spoken_reply_fallback_keeps_buttons_on_the_text(voice_env):
    client, _, chat, _, api, talk, _ = voice_env
    chat.reply_audio_text = "Hola."
    chat.state = {**chat.state, "manual_actions": [_action("go", "Go")]}
    api.fail_upload = True
    _post(client, _payload(mtype="audio"))
    assert api.timeline == ["typing", "buttons"]
    assert api.interactive[0][2] == "*Hola* — has dicho: hola por voz"


# --- audio encoding ------------------------------------------------------- #

def test_split_wav_handles_streaming_header_and_complete_file():
    pcm, rate = split_wav(_wav(rate=24000))
    assert rate == 24000 and len(pcm) == 12000 * 2  # 0.5 s of 16-bit mono
    streamed = PcmWavCodec.streaming_header(24000) + pcm
    assert split_wav(streamed) == (pcm, 24000)


def test_wav_to_mp3_produces_mono_48k_mp3():
    import av

    mp3 = wav_to_mp3(_wav(seconds=1.0))
    assert mp3[:3] == b"ID3"  # PyAV's mp3 muxer always writes an ID3v2 header
    container = av.open(io.BytesIO(mp3))
    try:
        stream = container.streams.audio[0]
        assert stream.codec_context.name.startswith("mp3")
        assert stream.rate == 48000 and stream.layout.name == "mono"
    finally:
        container.close()
    assert len(mp3) < len(_wav(seconds=1.0)) // 3


def test_incremental_encoder_matches_whole_file_encoding():
    """Pushing the stream in arbitrary pieces — the header split too —
    must decode to the same audio as encoding the complete WAV at once."""
    import av
    from whatsapp.audio import Mp3Encoder

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


def test_incremental_encoder_with_no_audio_finishes_empty():
    from whatsapp.audio import Mp3Encoder

    encoder = Mp3Encoder()
    encoder.push(PcmWavCodec.streaming_header(22050))
    assert encoder.finish() == b""


def test_wav_to_mp3_rejects_empty_audio():
    with pytest.raises(ValueError):
        wav_to_mp3(PcmWavCodec.streaming_header(22050))


# --- helpers -------------------------------------------------------------- #

def test_markdown_flattening():
    src = "## Título\n\nHola **fuerte** y __otro__, mira [esto](https://x.y).\n\n* uno\n* dos\n- tres"
    assert to_whatsapp_markdown(src) == "*Título*\n\nHola *fuerte* y *otro*, mira esto (https://x.y).\n\n- uno\n- dos\n- tres"


def test_split_text_respects_limit_and_loses_nothing():
    text = ("palabra " * 1000).strip()
    chunks = split_text(text, 4096)
    assert len(chunks) == 2 and all(len(c) <= 4096 for c in chunks)
    assert " ".join(chunks) == text
