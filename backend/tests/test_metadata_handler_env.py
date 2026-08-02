"""Tests for MetadataHandler's own [env] handling: parsing incoming
[env]...[/env] content (a forgiving "key: value" per line format, not
JSON — see _parse_env_tag) out of a model reply, and rendering it back
into the turn's own prompt via build_prompt.
"""
from __future__ import annotations

from datetime import datetime

from automaton.automaton import Action, Automaton, Signal, State
from chat.env import Env
from chat.metadata_handler import MetadataHandler
from chat.signals import Signals

USERNAME = "user"
PROJECT_NAME = "proj"


def _empty_automaton() -> Automaton:
    init_action = Action(name="init_action", ui_label="init_action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]),
                "a": State(key="a", ui_label="A", final=True, contextual_prompt="hi")},
        general_prompt="",
        signals=[],
        attachments={},
        general_attachments={},
        autotracking_on_user_message=True,
        autotracking_on_ai_message=False,
    )


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
    signals = Signals(get_active_automaton=_empty_automaton, db=db)
    env = Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)

    prompt = _handler().build_prompt(signals, env)

    assert "[env]" in prompt and "[/env]" in prompt
    assert "favorite_color: blue" in prompt
    assert "number_of_user_sessions: 1" in prompt


def test_build_prompt_scopes_signal_definitions_via_signal_names(db):
    """signal_names (see Automaton.triggerable_signal_names) passes
    straight through to Signals.get_definition — the env block itself is
    unaffected either way."""
    automaton = Automaton(
        init_action=Action(name="init_action", ui_label="init_action", ui_button="", target="a"),
        states={"": State(key="", ui_label="", final=False), "a": State(key="a", ui_label="A", final=True, contextual_prompt="hi")},
        general_prompt="",
        signals=[
            Signal(name="mood", ui_label="Mood", definition="mood definition"),
            Signal(name="unused", ui_label="Unused", definition="unused definition"),
        ],
        attachments={}, general_attachments={},
        autotracking_on_user_message=True, autotracking_on_ai_message=False,
    )
    signals = Signals(get_active_automaton=lambda: automaton, db=db)
    env = Env(db, get_username=lambda: USERNAME, get_active_project_name=lambda: PROJECT_NAME)

    prompt = _handler().build_prompt(signals, env, signal_names={"mood"})

    assert "mood definition" in prompt
    assert "unused definition" not in prompt
