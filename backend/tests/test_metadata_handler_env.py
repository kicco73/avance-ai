"""Tests for MetadataHandler's [env]/[signals]/[audio] parsing (a
forgiving "key: value"-per-line format, not JSON), and for how those
values, together with a signal definition, get rendered into a turn's prompt.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from tracking.fixed_project_context import FixedProjectContext
from tracking.env import PersistedEnv
from tracking.metadata_handler import MetadataHandler
from tracking.turn_protocol import TurnProtocol

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
    """A throwaway TurnProtocol subclass that captures whatever prompt
    __build_prompt assembled, instead of sending it to a real/fake AI
    service."""

    # Needs its own entries for every tag __build_prompt might look up,
    # since the base class default ({}) doesn't define any.
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
        # Not exercised here — only present so this subclass stays instantiable.
        raise NotImplementedError


def test_generate_reply_renders_an_env_block_with_stored_values_only(db):
    """System/session facts live in the `system`/`session`
    evaluation-scope namespaces, never rendered into the prompt."""
    db.ensure_project(PROJECT_NAME)
    db.publish_project(PROJECT_NAME)
    db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )
    db.set_env(PROJECT_NAME, {"favorite_color": "blue"}, USERNAME)
    env = PersistedEnv(db, FixedProjectContext(project_name=PROJECT_NAME))
    protocol = _RecordingProtocol()

    protocol.generate_reply("base prompt", "- Definition of signals:\n", env, [], lambda k, v: None)

    prompt = protocol.recorded_prompt
    assert "favorite_color: blue" in prompt
    assert "number_of_user_sessions" not in prompt
    assert "today:" not in prompt


def test_generate_reply_embeds_the_given_signal_definition_verbatim(db):
    """__build_prompt takes the already-rendered definition text directly
    — it has no opinion of its own on which signals it describes."""
    env = PersistedEnv(db, FixedProjectContext(project_name=PROJECT_NAME))
    protocol = _RecordingProtocol()

    protocol.generate_reply(
        "base prompt", '- Definition of signals:\n\t- Signal "mood":\nmood definition', env, [], lambda k, v: None
    )

    assert "mood definition" in protocol.recorded_prompt
