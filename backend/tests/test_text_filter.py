"""Tests for chat.text_filter.StreamingTagFilter/ConcatTagFilter.

Regression coverage for a real observed bug: a model reply that opens a
tag (most commonly [audio], since it's meant to come "at the very
beginning" of every reply — see MetadataHandler.EMBED_METADATA_PROMPT)
but never closes it used to silently swallow everything after it,
including the actual visible answer, leaving the user with an empty chat
bubble while e.g. audio_text ended up holding a garbled dump of
everything that came after. flush() now recovers that leftover content
instead of discarding it.
"""
from __future__ import annotations

from chat.text_filter import ConcatTagFilter, StreamingTagFilter


def test_well_formed_tag_is_extracted_and_stripped():
    f = StreamingTagFilter("[audio]", "[/audio]")
    visible = f.filter("hi [audio]spoken bit[/audio] there") + f.flush()
    assert visible == "hi  there"
    assert f.tag_content == "spoken bit"


def test_unclosed_tag_recovers_its_content_on_flush_instead_of_losing_it():
    f = StreamingTagFilter("[audio]", "[/audio]")
    visible = f.filter("hi [audio]never closes") + f.flush()
    assert visible == "hi never closes"
    # No close tag was ever seen — nothing to report as the "real" audio content.
    assert f.tag_content == ""


def test_unclosed_tag_recovers_a_trailing_false_start_too():
    """A partial close-tag prefix ("[/aud") still sitting in `pending`
    when the stream ends must also be recovered, not just tag_content."""
    f = StreamingTagFilter("[audio]", "[/audio]")
    visible = f.filter("hi [audio]stuff[/aud") + f.flush()
    assert visible == "hi stuff[/aud"


def test_concat_filter_full_reply_with_all_three_tags():
    reply = (
        '[audio]Ciao, come va?[/audio]'
        'Ecco la mia risposta visibile qui.'
        '[signals]{"foo": 1}[/signals]'
        '[env]\nmood: happy\n[/env]'
    )
    f = ConcatTagFilter("audio", "signals", "env")
    visible = f.filter_and_flush(reply)

    assert visible == "Ecco la mia risposta visibile qui."
    assert f.tags["audio"].tag_content == "Ciao, come va?"


def test_concat_filter_recovers_the_whole_reply_when_audio_never_closes():
    """The actual bug: an unclosed [audio] tag used to swallow the real
    answer AND the signals/env tags right along with it, leaving an empty
    visible reply. Now the real answer is recovered, and signals/env
    (embedded in what [audio] would have swallowed) are still correctly
    found and stripped by the later filters in the same chain."""
    reply = (
        '[audio]Ciao, come va? Ecco la mia risposta visibile qui.'
        '[signals]{"foo": 1}[/signals]'
        '[env]\nmood: happy\n[/env]'
    )
    f = ConcatTagFilter("audio", "signals", "env")
    visible = f.filter_and_flush(reply)

    assert visible == "Ciao, come va? Ecco la mia risposta visibile qui."
    # The tag never closed — no clean audio blurb was ever confirmed, so
    # this must be empty/falsy, not a garbled dump of everything after it
    # (see chat_service.py's own `audio_text = ... tag_content or None`).
    assert f.tags["audio"].tag_content == ""


def test_concat_filter_recovers_correctly_when_streamed_in_small_chunks():
    """Same scenario as above, but fed one character at a time — the
    realistic streaming shape (see TurnStrategyV1._receive_ai_stream_and_
    sendreply, which calls filter() per chunk and flush() only once, at
    the very end)."""
    reply = (
        '[audio]Ciao, come va? Ecco la mia risposta visibile qui.'
        '[signals]{"foo": 1}[/signals]'
        '[env]\nmood: happy\n[/env]'
    )
    f = ConcatTagFilter("audio", "signals", "env")
    streamed = ""
    for ch in reply:
        streamed += f.filter(ch)
    streamed += f.flush()

    assert streamed == "Ciao, come va? Ecco la mia risposta visibile qui."


def test_concat_filter_well_formed_reply_survives_streaming_too():
    reply = (
        '[audio]Ciao, come va?[/audio]'
        'Ecco la mia risposta visibile qui.'
        '[signals]{"foo": 1}[/signals]'
        '[env]\nmood: happy\n[/env]'
    )
    f = ConcatTagFilter("audio", "signals", "env")
    streamed = ""
    for ch in reply:
        streamed += f.filter(ch)
    streamed += f.flush()

    assert streamed == "Ecco la mia risposta visibile qui."
    assert f.tags["audio"].tag_content == "Ciao, come va?"


def test_concat_filter_recovers_when_signals_never_closes():
    """The tail-end equivalent: if a *later* tag never closes, only the
    trailing content after it is affected — earlier visible text and
    earlier tags are unaffected either way."""
    reply = (
        '[audio]Ciao[/audio]'
        'Ecco la mia risposta.'
        '[signals]{"foo": 1}'  # never closes
    )
    f = ConcatTagFilter("audio", "signals", "env")
    visible = f.filter_and_flush(reply)

    assert visible == 'Ecco la mia risposta.{"foo": 1}'
    assert f.tags["audio"].tag_content == "Ciao"
