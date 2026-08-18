"""Tests for MetadataHandler's own [env]/[signals]/[audio] parsing (a
forgiving "key: value"-per-line format for env, not JSON — see
parse_raw_env), and for how those values, together with a signal
definition, get rendered back into a turn's own prompt.

Renamed/moved for this refactor: MetadataHandler._parse_env_tag became
the public, static MetadataHandler.parse_raw_env (see tracking/
metadata_handler.py:24-43) — same forgiving parsing rules, just renamed.
MetadataHandler.build_prompt no longer exists at all: prompt assembly
(base prompt + env block + signal definition, tag-wrapped for v1 or
schema-embedded for v2) moved to TurnProtocol.__build_prompt, driven by
TurnProtocol.generate_reply (see tracking/turn_protocol.py:21-45) —
tested here via a recording TurnProtocol subclass, since that private
method has no public seam of its own.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from tracking.env import PersistedEnv
from tracking.metadata_handler import MetadataHandler
from tracking.turn_protocol import TurnProtocol

# Every test here is about the interface/format this module must keep
# respecting: the forgiving env-line parsing rules, and the shape of
# what gets rendered back into a turn's own prompt.
pytestmark = pytest.mark.contract

USERNAME = "user"
PROJECT_NAME = "proj"


def _handler() -> MetadataHandler:
    return MetadataHandler()


def test_parse_raw_env_reads_plain_key_value_lines():
    result = _handler().parse_raw_env("favorite_color: blue\nmood: happy")
    assert result == {"favorite_color": "blue", "mood": "happy"}


def test_parse_raw_env_strips_a_leading_dash_bullet():
    result = _handler().parse_raw_env("- favorite_color: blue\n- mood: happy")
    assert result == {"favorite_color": "blue", "mood": "happy"}


def test_parse_raw_env_ignores_blank_lines_and_lines_without_a_colon():
    result = _handler().parse_raw_env("favorite_color: blue\n\njust some noise\nmood: happy")
    assert result == {"favorite_color": "blue", "mood": "happy"}


def test_parse_raw_env_of_empty_content_is_an_empty_dict():
    assert _handler().parse_raw_env("") == {}
    assert _handler().parse_raw_env(None) == {}


def test_parse_raw_env_handles_a_colon_inside_the_value():
    result = _handler().parse_raw_env("next_meeting: 14:30")
    assert result == {"next_meeting": "14:30"}




class _RecordingProtocol(TurnProtocol):
    """A throwaway TurnProtocol subclass — TurnProtocol.__build_prompt is
    a private (name-mangled) method with no public seam of its own,
    called synchronously inside generate_reply before _generate_reply is
    ever invoked/iterated (see turn_protocol.py:32-33) — this just
    captures whatever prompt it was actually called with, instead of
    sending it on to a real/fake AI service at all."""

    # Real subclasses (TurnProtocolUsingSchema/TurnProcotolUsingTextExtraction)
    # each define their own prompt_preambles covering every tag they
    # support — the base class default ({}) doesn't, so this throwaway
    # subclass needs its own entries for every tag __build_prompt might
    # look up (see turn_protocol.py:39-45's `self.prompt_preambles[tag]`).
    prompt_preambles = {"env": "ENV:\n", "audio": "AUDIO:\n", "signals": "SIGNALS:\n", "text": ""}

    def __init__(self) -> None:
        super().__init__(ai_service=None, evaluate_signals_first=True)
        self.recorded_prompt: str | None = None

    def _generate_reply(self, prompt, chat_history, on_metadata):
        self.recorded_prompt = prompt

        async def _empty():
            return
            yield  # pragma: no cover - never reached, keeps this an async generator

        return _empty()

    def generate_reply_with_schema(self, base_prompt, tag_specs, chat_history, on_metadata):
        # Not exercised by this file's tests — only present so this
        # throwaway subclass stays instantiable now that TurnProtocol
        # declares it as abstract (see tracking/turn_protocol.py).
        raise NotImplementedError


def test_generate_reply_renders_an_env_block_with_stored_and_computed_values(db):
    db.ensure_project(PROJECT_NAME)
    db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )
    db.set_env(PROJECT_NAME, {"favorite_color": "blue"}, USERNAME)
    env = PersistedEnv(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    protocol = _RecordingProtocol()

    protocol.generate_reply("base prompt", "- Definition of signals:\n", env, [], lambda k, v: None)

    prompt = protocol.recorded_prompt
    assert "favorite_color: blue" in prompt
    assert "number_of_user_sessions: 1" in prompt


def test_generate_reply_embeds_the_given_signal_definition_verbatim(db):
    """TurnProtocol.__build_prompt takes the already-rendered definition
    text directly (see tracking.definitions.Signals.get_definition) — it
    has no opinion of its own on which signals that text describes, just
    embeds whatever string it's given."""
    env = PersistedEnv(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)
    protocol = _RecordingProtocol()

    protocol.generate_reply(
        "base prompt", '- Definition of signals:\n\t- Signal "mood":\nmood definition', env, [], lambda k, v: None
    )

    assert "mood definition" in protocol.recorded_prompt
