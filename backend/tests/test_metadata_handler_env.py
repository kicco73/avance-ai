"""Tests for MetadataHandler's own [env] handling: parsing incoming
[env]...[/env] content (a forgiving "key: value" per line format, not
JSON — see _parse_env_tag) out of a model reply, and rendering it back
into the turn's own prompt via build_prompt.
"""
from __future__ import annotations

from datetime import datetime

from chat.env import Env
from chat.metadata_handler import MetadataHandler

USERNAME = "user"
PROJECT_NAME = "proj"


def _handler() -> MetadataHandler:
    return MetadataHandler()


def test_parse_env_tag_reads_plain_key_value_lines():
    result = _handler()._parse_env_tag("favorite_color: blue\nmood: happy")
    assert result == {"favorite_color": "blue", "mood": "happy"}


def test_parse_env_tag_strips_a_leading_dash_bullet():
    result = _handler()._parse_env_tag("- favorite_color: blue\n- mood: happy")
    assert result == {"favorite_color": "blue", "mood": "happy"}


def test_parse_env_tag_ignores_blank_lines_and_lines_without_a_colon():
    result = _handler()._parse_env_tag("favorite_color: blue\n\njust some noise\nmood: happy")
    assert result == {"favorite_color": "blue", "mood": "happy"}


def test_parse_env_tag_of_empty_content_is_an_empty_dict():
    assert _handler()._parse_env_tag("") == {}
    assert _handler()._parse_env_tag(None) == {}


def test_parse_env_tag_handles_a_colon_inside_the_value():
    result = _handler()._parse_env_tag("next_meeting: 14:30")
    assert result == {"next_meeting": "14:30"}


def test_filter_text_and_extract_tags_extracts_a_parsed_env_dict():
    reply = "hello [audio]hi there[/audio][avance]{}[/avance][env]mood: happy[/env]"
    visible, tags = _handler()._filter_text_and_extract_tags(reply)
    assert visible == "hello "
    assert tags["env"] == {"mood": "happy"}


def test_build_prompt_renders_an_env_block_with_stored_and_computed_values(db):
    db.create_chat_session(
        username=USERNAME, project_name=PROJECT_NAME,
        datetime_start=datetime(2026, 1, 1), datetime_end=datetime(2026, 1, 1),
        start_state="a", end_state="a",
    )
    db.set_env(PROJECT_NAME, {"favorite_color": "blue"}, USERNAME)
    env = Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)

    prompt = _handler().build_prompt("- Definition of signals:\n", env)

    assert "[env]" in prompt and "[/env]" in prompt
    assert "favorite_color: blue" in prompt
    assert "number_of_user_sessions: 1" in prompt


def test_build_prompt_embeds_the_given_signal_definition_verbatim(db):
    """build_prompt takes the already-rendered definition text directly
    (see signals.definitions.Signals.get_definition/signals.evaluator.
    SignalEvaluator.compute_explicitly, which resolve and scope it before
    calling this) — it has no opinion of its own on which signals that
    text describes, just embeds whatever string it's given."""
    env = Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)

    prompt = _handler().build_prompt('- Definition of signals:\n\t- Signal "mood":\nmood definition', env)

    assert "mood definition" in prompt
