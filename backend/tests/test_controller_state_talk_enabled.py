"""GET /api/state's own talk_enabled — the AND of "does the server have
a TTS provider configured at all" (talk_service is not None) and "does
the active project itself opt in" (its own automaton.talk_enabled,
defaulting true) — the chat toolbar's audio/spoken-text icons
(ChatInput.vue) read this one combined flag.
"""
from __future__ import annotations

import pytest

from controllers.chat_controller import ChatController

pytestmark = pytest.mark.contract


class _FakeAutomaton:
    def __init__(self, talk_enabled: bool) -> None:
        self.talk_enabled = talk_enabled


class _FakeProjectService:
    def __init__(self, talk_enabled: bool) -> None:
        self._automaton = _FakeAutomaton(talk_enabled)

    def get_active_state_payload(self) -> dict:
        return {}

    def get_active_automaton(self):
        return self._automaton


class _NoActiveProjectService:
    def get_active_state_payload(self):
        raise ValueError("no active project")

    def get_active_automaton(self):
        raise ValueError("no active project")


class _FakeChatService:
    def get_input_token_budget_per_turn(self) -> int:
        return 16000

    def get_total_token_budget_per_session(self) -> int:
        return 200000


def _controller(*, talk_service_configured: bool, project_talk_enabled: bool) -> ChatController:
    return ChatController(
        chat_service=_FakeChatService(),
        project_service=_FakeProjectService(project_talk_enabled),
        talk_service=object() if talk_service_configured else None,
        listen_service=None,
    )


def test_talk_enabled_when_server_has_a_provider_and_the_project_opts_in():
    controller = _controller(talk_service_configured=True, project_talk_enabled=True)
    assert controller.get_state()["talk_enabled"] is True


def test_talk_disabled_when_the_project_opts_out_even_with_a_server_provider():
    controller = _controller(talk_service_configured=True, project_talk_enabled=False)
    assert controller.get_state()["talk_enabled"] is False


def test_talk_disabled_when_the_server_has_no_provider_even_if_the_project_opts_in():
    controller = _controller(talk_service_configured=False, project_talk_enabled=True)
    assert controller.get_state()["talk_enabled"] is False


def test_defaults_to_the_server_flag_when_there_is_no_active_project():
    controller = ChatController(
        chat_service=_FakeChatService(),
        project_service=_NoActiveProjectService(),
        talk_service=object(),
        listen_service=None,
    )
    assert controller.get_state()["talk_enabled"] is True
