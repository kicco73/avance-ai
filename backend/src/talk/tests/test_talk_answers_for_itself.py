"""Two questions only this package can answer, and it answers both by
contributing rather than by being asked about by name.

`talk_enabled` on GET /api/core/state is what the active project's own
declared level (project.services.talk, see automaton/project_services.py)
makes of "does this server have a TTS provider at all". A project can
only narrow it, never turn it on; the chat toolbar's audio/spoken-text
icons read that one combined flag.

Whether a turn asks the model for a spoken version of its reply is the
same question with the turn's own say added — a session with audio off
should not pay for the extra field.

Both used to be computed in core, from the string "talk": the state
controller read `services["talk"]` and a `talk_configured()` helper, and
TrackingService took a `talk_enabled` argument threaded down from
main.py. Core knew the name of a package a build is meant to be able to
ship without.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from automaton.project_services import ProjectServices
from system import bus
from system.bus import POINT_API_STATE, POINT_CORE_SERVICES, POINT_SPOKEN_REPLY
from system.api_state_controller import ApiStateController
from talk.skill import TalkSkill
from tracking.spoken_reply import SpokenReply

pytestmark = pytest.mark.contract

#: The smallest .config.yml that gives this server a TTS provider (see
#: talk/config.py, and config.py's own _get_optional_providers: a section
#: without `enabled` is off however many providers it lists).
_CONFIGURED = {
    "talk-service": {
        "enabled": True,
        "providers": [{"driver": "piper", "model": "en_US-amy-medium", "ui-label": "Amy"}],
    },
}


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


def _started(monkeypatch, *, provider_configured: bool, project_service) -> TalkSkill:
    """The skill as boot leaves it: the real start_service, with only the
    synthesizer stubbed — a configured piper provider loads a voice model
    off disk, which a test has no business having, and which is exactly
    why the answers below are registered before anything is installed."""
    import talk.skill as skill_module

    monkeypatch.setitem(skill_module._INSTALLATIONS, True, skill_module._NoTalk)
    bus.contribute(POINT_CORE_SERVICES, lambda registry: registry.update({"project_service": project_service}))
    skill = TalkSkill()
    skill.start_service(_CONFIGURED if provider_configured else {}, Path("."))
    return skill


def _state(project_service) -> dict:
    controller = ApiStateController(turn_service=_FakeChatService(), project_service=project_service)
    return controller.get_state()


@pytest.mark.parametrize("project_level, provider, expected", [
    ("required", True, True),
    (None, True, True),
    ("disabled", True, False),
    ("required", False, False),
    (None, False, False),
], ids=["required", "undeclared", "project-disables", "no-provider", "neither"])
def test_the_state_flag_is_the_project_narrowing_the_server_s_own_switch(monkeypatch, project_level, provider, expected):
    project_service = _FakeProjectService(project_level)
    _started(monkeypatch, provider_configured=provider, project_service=project_service)

    assert _state(project_service)["talk_enabled"] is expected


def test_no_active_project_declares_nothing_which_leaves_the_server_flag(monkeypatch):
    project_service = _NoActiveProjectService()
    _started(monkeypatch, provider_configured=True, project_service=project_service)

    assert _state(project_service)["talk_enabled"] is True


def test_a_build_without_this_package_has_no_such_field_at_all():
    """Not False: absent. Nobody contributes it, and the state controller
    adds nothing about talk of its own — which is the whole point of the
    move."""
    payload = _state(_FakeProjectService("required"))

    assert "talk_enabled" not in payload


@pytest.mark.parametrize("project_level, provider, wanted, expected", [
    ("required", True, True, True),
    (None, True, True, True),
    ("disabled", True, True, False),
    ("required", False, True, False),
    ("required", True, False, False),
], ids=["required", "undeclared", "project-disables", "no-provider", "turn-does-not-want-it"])
def test_whether_a_turn_asks_for_a_spoken_reply(monkeypatch, project_level, provider, wanted, expected):
    _started(monkeypatch, provider_configured=provider, project_service=_FakeProjectService(project_level))

    spoken = bus.collect(POINT_SPOKEN_REPLY, SpokenReply(
        services=ProjectServices({"talk": project_level} if project_level else {}), wanted=wanted,
    ))

    assert spoken.asked is expected


def test_nobody_answering_means_nothing_speaks():
    """A build without this package registers no contributor, and an
    unanswered SpokenReply stays unasked. That is the answer core needs,
    and it never had to know whose absence produced it."""
    spoken = bus.collect(POINT_SPOKEN_REPLY, SpokenReply(services=ProjectServices({}), wanted=True))

    assert spoken.asked is False


def test_the_state_controller_still_collects_contributions_at_all():
    """A controller that stopped collecting would pass every check above
    that asserts a field is absent."""
    bus.contribute(POINT_API_STATE, lambda payload: payload.update({"proof": True}))

    assert _state(_FakeProjectService(None))["proof"] is True
