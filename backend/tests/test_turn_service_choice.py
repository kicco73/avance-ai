"""A `choice` env key whose options a state's trigger reads is a row of
buttons, one per option (TurnService.buttons_for); pressing one is the
value of `choice.<key>` for the single trigger evaluation it starts
(TurnService.apply_choice) — it writes nothing itself, the action that
fires transitions like a manual one.
"""
from __future__ import annotations

import asyncio

import pytest

from automaton.automaton import Action, Automaton, EnvKey, State
from automaton.choice import ChoiceSelection
from db.models import Tracking
from system import bus
from system.bus import INPUT_BUTTON, Message
from system.web_session import WebSession
from tracking.env import PersistedEnv
from tracking.fixed_project_context import FixedProjectContext
from turn.input_listener import TurnInput
from turn.sessions.env_for_session import env_for_session
from turn_harness import PROJECT_ID, turn_service_for  # noqa: F401 — a fixture, used by name

pytestmark = pytest.mark.regression

_PUBLISHED = ("output.text", "state.changed", "env.changed", "state.buttons", "output.error")


class _FakeProvider:
    async def generate_stream_with_schema(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ):
        yield '{"text": "Booked."}'

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0

    def get_max_output_tokens(self) -> int:
        return 4096


def _automaton(trigger: str = "choice.slot != ''") -> Automaton:
    manual = Action(name="manual", ui_label="Manual", ui_button="Manual", target="a")
    auto = Action(name="auto", ui_label="Auto", ui_button="Auto", target="a", trigger="False")
    book = Action(
        name="book", ui_label="Book", ui_button="Book", target="a", trigger=trigger,
        env={"booked_slot": "choice.slot"}, on_exit="env.note = 'picked ' + choice.slot",
    )
    init_action = Action(name="init-action", ui_label="init-action", ui_button="", target="a")
    state_a = State(
        key="a", ui_label="A", final=False, contextual_prompt="hi", actions=[manual, auto, book], choice_keys=("slot",),
    )
    return Automaton(
        init_action=init_action,
        states={"": State(key="", ui_label="", final=False, actions=[init_action]), "a": state_a},
        general_prompt="", signals=[], general_attachments=(), autotracking_on_ai_message=False,
        project_id=PROJECT_ID,
        env_keys=[
            EnvKey(name="slot", type="list", ai_definition="The appointment slot."),
            EnvKey(name="booked_slot", type="string"),
            EnvKey(name="note", type="string"),
        ],
    )


def _env_for(db, session_id: int) -> PersistedEnv:
    return PersistedEnv(db, FixedProjectContext(project_id=PROJECT_ID), session_id)


async def _session_with_options(turn_service, db, options: list[str], session_type: str = "live") -> int:
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    session = await turn_service.enter_session(PROJECT_ID, session_type)
    env_for_session(db, db.get_chat_session(session["id"])).update_action_set({"slot": options})
    return session["id"]


async def test_buttons_are_the_pressable_actions_then_one_per_current_option_of_each_choice_key(turn_service_for):
    db = turn_service_for.db
    turn_service = turn_service_for(_automaton(), _FakeProvider())
    session_id = await _session_with_options(turn_service, db, ["morning", "evening"])

    buttons = turn_service.buttons_for(session_id, turn_service.get_state_for_session(session_id))

    assert [button["name"] for button in buttons] == ["manual", "choice:slot:0", "choice:slot:1"]
    evening = buttons[2]
    assert (evening["ui_button"], evening["ui_label"], evening["ui_description"]) == (
        "evening", "evening", "The appointment slot.",
    )
    assert (evening["target"], evening["has_trigger"], evening["task"], evening["on-exit"]) == ("", False, None, None)


@pytest.mark.parametrize("options", [[], None], ids=["empty-list", "key-absent"])
async def test_no_option_means_no_choice_button(turn_service_for, options):
    db = turn_service_for.db
    turn_service = turn_service_for(_automaton(), _FakeProvider())
    session_id = await _session_with_options(turn_service, db, options or [])
    if options is None:
        _env_for(db, session_id).drop_action_set_keys({"slot"})

    buttons = turn_service.buttons_for(session_id, turn_service.get_state_for_session(session_id))

    assert [button["name"] for button in buttons] == ["manual"]


class _Recorder:
    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.finished = asyncio.Event()

    async def take(self, message: Message) -> None:
        self.messages.append(message)
        if message.type in ("output.text", "output.error"):
            self.finished.set()


async def _press(turn_service, db, session_id: int, button: str, timeout: float = 10) -> list[Message]:
    recorder = _Recorder()
    for message_type in _PUBLISHED:
        bus.subscribe(message_type, recorder.take)
    TurnInput(turn_service, db).register()
    await bus.publish(Message(
        type=INPUT_BUTTON, body={"id": button}, username=WebSession().user,
        session_id=session_id, channel="webchat", origin_id="connection-1",
    ))
    try:
        await asyncio.wait_for(recorder.finished.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    return recorder.messages


def _manual_rows(session_id: int) -> list[Tracking]:
    return list(Tracking.select().where((Tracking.session == session_id) & (Tracking.origin == "manual")))


async def test_pressing_an_option_fires_the_action_whose_trigger_reads_it_as_a_manual_transition(turn_service_for):
    db = turn_service_for.db
    turn_service = turn_service_for(_automaton(), _FakeProvider())
    session_id = await _session_with_options(turn_service, db, ["morning", "evening"])

    frames = await _press(turn_service, db, session_id, "choice:slot:1")

    by_type = {frame.type: frame.body for frame in frames}
    assert by_type["state.changed"]["triggered_action"] == "book"
    written = {frame.body["key"]: frame.body["value"] for frame in frames if frame.type == "env.changed"}
    assert written == {"booked_slot": "evening", "note": "picked evening"}
    assert _env_for(db, session_id).action_set()["booked_slot"] == "evening"
    assert _env_for(db, session_id).action_set()["slot"] == ["morning", "evening"]
    assert [(row.old_state, row.action, row.new_state) for row in _manual_rows(session_id)] == [("a", "book", "a")]


async def test_a_press_no_trigger_answers_to_publishes_nothing_and_records_nothing(turn_service_for):
    db = turn_service_for.db
    turn_service = turn_service_for(_automaton(trigger="choice.slot == 'never'"), _FakeProvider())
    session_id = await _session_with_options(turn_service, db, ["morning"])

    frames = await _press(turn_service, db, session_id, "choice:slot:0", timeout=1)

    assert frames == []
    assert _manual_rows(session_id) == []
    assert _env_for(db, session_id).action_set()["booked_slot"] == ""


@pytest.mark.parametrize("button", ["choice:slot:5", "choice:nowhere:0"], ids=["index-out-of-range", "key-not-offered"])
async def test_a_press_naming_no_current_option_is_refused_as_choice_unavailable(turn_service_for, button):
    db = turn_service_for.db
    turn_service = turn_service_for(_automaton(), _FakeProvider())
    session_id = await _session_with_options(turn_service, db, ["morning"])

    frames = await _press(turn_service, db, session_id, button)

    assert [frame.type for frame in frames] == ["output.error"]
    assert frames[0].body["code"] == "choice_unavailable"
    assert _manual_rows(session_id) == []


async def test_a_key_the_state_does_not_read_is_refused_even_when_env_holds_options(turn_service_for):
    db = turn_service_for.db
    automaton = _automaton()
    automaton.env_keys.append(EnvKey(name="unread", type="list"))
    turn_service = turn_service_for(automaton, _FakeProvider())
    session_id = await _session_with_options(turn_service, db, ["morning"])
    _env_for(db, session_id).update_action_set({"unread": ["x"]})

    with pytest.raises(ValueError, match="not a choice offered in state 'a'"):
        await turn_service.apply_choice(ChoiceSelection(key="unread", option="x"), session_id)
