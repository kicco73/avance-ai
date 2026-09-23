from __future__ import annotations

import asyncio

import pytest

from ai.llm_provider import AIServiceProviderUnavailableError, LLMProvider
from automaton.automaton import Action, Automaton, EnvKey, State
from system import bus
from system.bus import INPUT_BUTTON, Message
from system.web_session import WebSession
from tracking.env import PersistedEnv
from tracking.fixed_project_context import FixedProjectContext
from turn.input_listener import TurnInput
from turn_harness import PROJECT_ID, turn_service_for  # noqa: F401 — a fixture, used by name

pytestmark = pytest.mark.regression

_FRAMES = ("state.changed", "state.buttons", "output.error")


class _BusyOnce(LLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def stream_json(
        self, system_prompt, history, schema, on_metadata=None, tools=None, tool_round=1, required_tools=None,
    ):
        self.calls += 1
        if self.calls == 1:
            raise AIServiceProviderUnavailableError("503 busy")
        yield '{"text": "Your results.", "output": {"report": "# Report"}}'

    def get_total_tokens(self) -> int:
        return 0

    def get_input_tokens(self, prompt: str) -> int:
        return 0

    def get_max_output_tokens(self) -> int:
        return 4096


def _automaton() -> Automaton:
    end = Action(
        name="end", ui_label="End", ui_button="End", target="evaluation", trigger="choice.frequency",
        on_exit="env.score = env.score + 10",
    )
    question = State(
        input_processor="system", key="a", ui_label="Question", final=False, actions=[end],
        choice_keys=("frequency",), chat_enabled=False,
    )
    evaluation = State(
        input_processor="ai", key="evaluation", ui_label="Evaluation", final=True, contextual_prompt="Evaluate.",
        output=("report",), chat_enabled=False,
    )
    init_action = Action(name="init-action", ui_label="init-action", ui_button="", target="a")
    return Automaton(
        init_action=init_action,
        states={
            "": State(input_processor="system", key="", ui_label="", final=False, actions=[init_action]),
            "a": question, "evaluation": evaluation,
        },
        general_prompt="", signals=[], general_attachments=(), autotracking_on_ai_message=False, project_id=PROJECT_ID,
        env_keys=[
            EnvKey(name="frequency", type="list", ai_definition="How often."),
            EnvKey(name="score", type="number", ai_definition="Score."),
            EnvKey(name="report", type="string", ai_definition="The report."),
        ],
    )


def _env(db, session_id: int) -> PersistedEnv:
    return PersistedEnv(db, FixedProjectContext(project_id=PROJECT_ID), session_id)


async def _session(turn_service_for) -> tuple:
    db = turn_service_for.db
    turn_service = turn_service_for(_automaton(), _BusyOnce())
    db.get_or_create_user(None, None, WebSession().user, None, None, user_id=WebSession().user)
    session_id = (await turn_service.enter_session(PROJECT_ID, "live"))["id"]
    _env(db, session_id).update_action_set({"frequency": ["Never", "Everyday"], "score": 0})
    TurnInput(turn_service, db).register()
    return turn_service, db, session_id


async def _press(session_id: int, button: str) -> dict[str, dict]:
    frames: dict[str, dict] = {}
    finished = asyncio.Event()

    async def take(message: Message) -> None:
        frames[message.type] = message.body
        if message.type in ("state.buttons", "output.error"):
            finished.set()

    for frame_type in _FRAMES:
        bus.subscribe(frame_type, take)
    try:
        await bus.publish(Message(
            type=INPUT_BUTTON, body={"id": button}, username=WebSession().user,
            session_id=session_id, channel="webchat", origin_id="connection-1",
        ))
        await asyncio.wait_for(finished.wait(), timeout=10)
    finally:
        for frame_type in _FRAMES:
            bus.unsubscribe(frame_type, take)
    return frames


async def test_a_busy_provider_on_entering_an_ai_state_leaves_the_conversation_where_it_was(turn_service_for):
    turn_service, db, session_id = await _session(turn_service_for)

    frames = await _press(session_id, "choice:frequency:1")

    assert frames["output.error"]["code"] == "ai_provider_busy"
    assert "state.changed" not in frames
    state = turn_service.get_state_for_session(session_id)
    assert state["key"] == "a"
    assert [b["name"] for b in turn_service.buttons_for(session_id, state)] == ["choice:frequency:0", "choice:frequency:1"]
    assert _env(db, session_id).action_set()["score"] == 0


async def test_pressing_again_once_the_provider_answers_moves_on_and_runs_on_exit_once(turn_service_for):
    turn_service, db, session_id = await _session(turn_service_for)
    await _press(session_id, "choice:frequency:1")

    frames = await _press(session_id, "choice:frequency:1")

    assert frames["state.changed"]["state"]["key"] == "evaluation"
    assert turn_service.get_state_for_session(session_id)["key"] == "evaluation"
    env = _env(db, session_id).action_set()
    assert (env["score"], env["report"]) == (10, "# Report")
