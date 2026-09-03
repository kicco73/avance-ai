"""Collaborator behind `actuator.prompt(...)` (see ActuatorSet.prompt) —
built fresh by EvaluationScopeBuilder.build for whichever (automaton,
state, session) an on-enter evaluation is running against, the same
"rebuilt every call, never threaded through __init__" reasoning as that
module's own SourceNamespace(db, automaton)."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from logging_factory import LoggerFactory
from tracking.priming import build_priming_messages

if TYPE_CHECKING:
    from ai.ai_service import AiService
    from automaton.automaton import Automaton, State
    from db.db import Db
    from tracking.env import Env

logger = LoggerFactory.get_logger(__name__)

# Same default as TrackingProcessor's own input_token_budget_per_turn —
# there's no per-project config threaded down to this constructor, so a
# single reasonable ceiling stands in for it here.
_HISTORY_TOKEN_BUDGET = 16000


def _signal_definition(automaton: "Automaton", state: "State") -> str | None:
    """Same formatting as tracking.definitions.Signals.get_definition,
    read straight off `automaton` instead — pulling in the real Signals
    class here would drag tracking.actuators into a project_service ->
    automaton_builder -> tracking.actuators import cycle (automaton_builder
    already imports this package for its own arity validation)."""
    names = automaton.triggerable_signal_names(state.key)
    relevant = [s for s in automaton.signals if s.name in names]
    if not relevant:
        return None
    return "- Definition of signals:\n" + "\n\n".join(
        f'\t- Signal "{s.name}":\n{s.definition}' for s in relevant
    )


def _run_sync(coro) -> str:
    """Blocks the calling (event-loop) thread until `coro` finishes,
    running it on a brand-new event loop of its own on a separate thread
    — asyncio.run()/run_until_complete() can't be used directly here
    since every caller of actuator.prompt() (on-enter evaluation) already
    runs synchronously from inside a coroutine that's mid-`await` on the
    real event loop thread, which forbids nesting another run on it."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


class PromptContext:
    """Everything actuator.prompt(text) needs to run one extra, read-only
    generation call synchronously and return its aggregated text — no
    message persisted, no env/signal/audio channel requested or applied
    (see ActuatorSet.prompt)."""

    def __init__(
        self,
        ai_service: "AiService",
        db: "Db",
        env: "Env",
        automaton: "Automaton",
        state: "State | None",
        session_id: int,
    ) -> None:
        self._ai_service = ai_service
        self._db = db
        self._env = env
        self._automaton = automaton
        self._state = state
        self._session_id = session_id

    def run(self, prompt: str) -> str:
        system_prompt = self._build_system_prompt(prompt)
        chat_history = self._build_chat_history()
        return _run_sync(self._ai_service.generate(system_prompt, chat_history))

    def _build_system_prompt(self, prompt: str) -> str:
        # `prompt` stands in for the state's own contextual-prompt here —
        # general-prompt is still included, the state's contextual-prompt
        # itself never is (see PROJECT_SPECS.md §6.5's actuator.prompt entry).
        parts = [self._automaton.general_prompt, prompt]
        if self._state is not None:
            signal_text = _signal_definition(self._automaton, self._state)
            if signal_text:
                parts.append(signal_text)
        env_text = self._env.serialise_as_text()
        if env_text:
            parts.append(f"Current env memory (context only, no response needed for this):\n{env_text}")
        return "\n\n".join(parts)

    def _build_chat_history(self) -> list[dict]:
        attachments = list(self._automaton.general_attachments.values())
        history_cutoff = False
        if self._state is not None:
            attachments += list(self._state.attachments.values())
            history_cutoff = self._state.history_cutoff
        since = self._db.history_cutoff_for_session(self._session_id, history_cutoff)
        history = self._db.get_turn_history(self._session_id, since, _HISTORY_TOKEN_BUDGET)
        stripped = [{"role": m["role"], "content": m["content"]} for m in history]
        return build_priming_messages(attachments) + stripped
