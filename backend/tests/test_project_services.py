"""The three levels a project declares per service, at run time.

`disabled` is not a flag anybody reads before posting: the level itself
does the posting, and a project that refuses a service reaches its own
caller exactly as a build with nobody listening does.
"""
from __future__ import annotations

import asyncio

import pytest

from automaton import project_services
from automaton.project_services import DisabledService, ProjectServices, RequiredService
from system import bus
from system.bus import Message

pytestmark = pytest.mark.contract

TYPE = "test.thing"


class _Sender:

    def __init__(self) -> None:
        self.bounced_back: list[Message] = []

    async def bounced(self, message: Message) -> None:
        self.bounced_back.append(message)


@pytest.fixture(autouse=True)
def clean_bus():
    bus._reset_for_tests()
    yield
    bus._reset_for_tests()


def _message() -> Message:
    return Message(type=TYPE, body="hello", username="alice")


def _subscribe() -> list[Message]:
    taken: list[Message] = []

    async def take(message: Message) -> None:
        taken.append(message)

    bus.subscribe(TYPE, take)
    return taken


def test_an_undeclared_service_is_optional():
    services = ProjectServices({"mail": "required"})

    assert services["listen"].name == project_services.OPTIONAL
    assert services["mail"].name == project_services.REQUIRED


def test_a_disabled_service_never_reaches_a_listener_that_is_there():
    taken = _subscribe()
    sender = _Sender()

    asyncio.run(DisabledService().deliver(_message(), sender))

    assert taken == []
    assert [m.body for m in sender.bounced_back] == ["hello"]


def test_a_required_or_optional_service_delivers_normally():
    taken = _subscribe()
    sender = _Sender()

    asyncio.run(RequiredService().deliver(_message(), sender))

    assert len(taken) == 1
    assert sender.bounced_back == []


def test_a_disabled_service_answers_false_without_publishing():
    taken = _subscribe()

    assert asyncio.run(DisabledService().publish(_message())) is False
    assert taken == []
    assert asyncio.run(RequiredService().publish(_message())) is True


def test_a_project_can_only_narrow_the_servers_own_switch():
    services = ProjectServices({"talk": "disabled", "listen": "required"})

    assert services["talk"].narrow(True) is False
    assert services["listen"].narrow(True) is True
    assert services["listen"].narrow(False) is False
    assert services["whatsapp"].narrow(True) is True


def test_a_level_that_is_not_one_of_the_three_is_a_build_error():
    with pytest.raises(ValueError, match="project.services.talk"):
        project_services.parse({"talk": "maybe"})


def test_the_deprecated_talk_flag_is_a_default_an_explicit_level_overrides():
    declared, warnings = project_services.parse({"talk": "optional"}, talk_enabled=True)

    assert declared.as_raw() == {"talk": "optional"}
    assert len(warnings) == 1
