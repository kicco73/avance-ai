"""The chat window's audio toggle, as this package's own answer.

Core used to read `is_audio_enabled` itself, on the way into every turn,
whatever channel the turn came from — one interface's switch answering on
behalf of all of them. Now the question is asked (bus.POINT_SPOKEN_REPLY)
and this package answers it for the sessions it runs.
"""
from __future__ import annotations

import pytest

from automaton.project_services import ProjectServices
from system import bus
from system.bus import POINT_SPOKEN_REPLY
from system.web_session import WebSession
from tracking.spoken_reply import SpokenReply
from webchat.webchat_service import WebchatService
from turn_harness import one_state_automaton, turn_service_for  # noqa: F401 — a pytest fixture, used by name

pytestmark = pytest.mark.contract

USERNAME = "user"


class _NoProvider:
    async def generate(self, *args, **kwargs):
        return
        yield


@pytest.fixture(autouse=True)
def _session_user():
    session = WebSession()
    previous = session.user
    session.user = USERNAME
    yield
    session.user = previous


def _wanted(session_id) -> bool:
    return bus.collect(
        POINT_SPOKEN_REPLY, SpokenReply(services=ProjectServices({}), session_id=session_id),
    ).wanted


async def test_only_a_session_with_the_toggle_on_wants_a_spoken_reply(turn_service_for):
    turn_service = turn_service_for(one_state_automaton(with_sources=False, autotracking_on_ai_message=False), _NoProvider())
    session = await turn_service.get_current_session_if_any_or_create_new(None)
    WebchatService(turn_service, None, None).register()

    assert _wanted(session["id"]) is False

    turn_service.set_audio_enabled(session["id"], True)
    assert _wanted(session["id"]) is True

    turn_service.set_audio_enabled(session["id"], False)
    assert _wanted(session["id"]) is False


async def test_a_turn_that_names_no_session_is_never_asked_about_one(turn_service_for):
    """A contributor that read the toggle for `None` would ask the
    ownership check for session None and take the turn down with it."""
    turn_service = turn_service_for(one_state_automaton(with_sources=False, autotracking_on_ai_message=False), _NoProvider())
    WebchatService(turn_service, None, None).register()

    assert _wanted(None) is False
