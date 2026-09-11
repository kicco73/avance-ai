"""GET /api/core/state's own talk_enabled — what the active project's own
declared level for the talk service (project.services.talk, see
automaton/project_services.py) makes of "does the server have a TTS
provider configured at all" (what the talk package itself declares at
bus.POINT_CONFIG_SERVICES). A project can only narrow it, never turn it
on; the chat toolbar's audio/spoken-text icons (ChatInput.vue) read this
one combined flag.
"""
from __future__ import annotations

import pytest

from automaton.project_services import ProjectServices
from system import bus
from system.bus import POINT_CONFIG_SERVICES
from system.api_state_controller import ApiStateController

pytestmark = pytest.mark.contract


def _declare_talk_configured() -> None:
    """Stands in for the talk skill's own _Talk.install(), which is what
    says "this server has a TTS provider" (see talk/config.py)."""
    bus.contribute(POINT_CONFIG_SERVICES, lambda snapshot: snapshot.update({"talk": {"enabled": True}}))


class _FakeAutomaton:
    def __init__(self, talk_level: str | None) -> None:
        self.services = ProjectServices({"talk": talk_level} if talk_level else {})


class _FakeInspector:
    def get_active_state_payload(self) -> dict:
        return {}


class _FakeProjectService:
    def __init__(self, talk_level: str | None) -> None:
        self._automaton = _FakeAutomaton(talk_level)
        self.inspector = _FakeInspector()

    def get_active_automaton(self):
        return self._automaton


class _NoInspector:
    def get_active_state_payload(self):
        raise ValueError("no active project")


class _NoActiveProjectService:
    def __init__(self) -> None:
        self.inspector = _NoInspector()

    def get_active_automaton(self):
        raise ValueError("no active project")


class _FakeChatService:
    def get_input_token_budget_per_turn(self) -> int:
        return 16000

    def get_total_token_budget_per_session(self) -> int:
        return 200000


def _controller(*, talk_service_configured: bool, project_talk_level: str | None) -> ApiStateController:
    if talk_service_configured:
        _declare_talk_configured()
    return ApiStateController(
        turn_service=_FakeChatService(),
        project_service=_FakeProjectService(project_talk_level),
    )


def test_talk_enabled_when_server_has_a_provider_and_the_project_requires_it():
    controller = _controller(talk_service_configured=True, project_talk_level="required")
    assert controller.get_state()["talk_enabled"] is True


def test_talk_enabled_when_server_has_a_provider_and_the_project_declares_nothing():
    controller = _controller(talk_service_configured=True, project_talk_level=None)
    assert controller.get_state()["talk_enabled"] is True


def test_talk_disabled_when_the_project_disables_it_even_with_a_server_provider():
    controller = _controller(talk_service_configured=True, project_talk_level="disabled")
    assert controller.get_state()["talk_enabled"] is False


def test_talk_disabled_when_the_server_has_no_provider_even_if_the_project_requires_it():
    controller = _controller(talk_service_configured=False, project_talk_level="required")
    assert controller.get_state()["talk_enabled"] is False


def test_defaults_to_the_server_flag_when_there_is_no_active_project():
    _declare_talk_configured()
    controller = ApiStateController(
        turn_service=_FakeChatService(),
        project_service=_NoActiveProjectService(),
    )
    assert controller.get_state()["talk_enabled"] is True
