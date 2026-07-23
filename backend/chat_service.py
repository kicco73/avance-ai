"""Transport-agnostic chat-turn logic, shared by both /ws/chat and POST
/api/messages (see conversation_controller.py and main.py). Building the
system prompt, running auto-tracking, calling the LLM provider with
retry, and persisting the result all live here exactly once; each
transport only knows how it receives a message and reports the outcome.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from automaton import Automaton, trigger_signal_names
from db import db
from llm_provider import (
    LLMProvider,
    LLMProviderError,
    LLMProviderRateLimitedError,
    LLMProviderUnavailableError,
    MAX_RETRIES,
    OnRetry,
    generate_with_retry,
)
from signals import Signals

logger = logging.getLogger(__name__)

# System prompt for states with a `fixed_message` (e.g. crisis): the model
# must translate it verbatim, not generate a free-form reply. Used for both
# a normal chat turn and an opening message landing on such a state.
FIXED_MESSAGE_INSTRUCTIONS = (
    "You must reply with ONLY a translation of the fixed message below into "
    "the same language the user's last message is written in. Do not answer "
    "or react to what the user said, do not add or remove anything, and do "
    "not change its meaning or formatting — output just the translation.\n\n"
    "Fixed message:\n{fixed_message}"
)

# Supplies the currently-active Automaton (models_manager.get_active_automaton)
# — passed in rather than imported: this module doesn't own which model is
# active, the same reason models_manager.py itself doesn't import main.py.
GetActiveAutomaton = Callable[[], Automaton]


class ChatServiceError(Exception):
    """The one error shape both transports translate from: a short
    readable `message`, an optional technical `detail`, and the HTTP
    status a REST caller should use — the same {message, detail} contract
    already uniform across the rest of the app. Websocket wraps this into
    an 'error' frame; REST into the standard JSON error body."""

    def __init__(self, message: str, detail: str | None = None, *, status_code: int = 500) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.status_code = status_code


@dataclass
class ChatTurnResult:
    reply: str
    state: dict
    state_changed: bool
    new_state: str | None
    triggered_action: str | None


class ChatService:
    def __init__(
        self,
        llm_provider: LLMProvider,
        get_active_automaton: GetActiveAutomaton,
    ) -> None:
        self._llm_provider = llm_provider
        self._get_active_automaton = get_active_automaton
        # Owned here, not imported as a shared singleton: constructed with
        # this same get_active_automaton, the only thing it needs.
        self.signals = Signals(get_active_automaton=get_active_automaton)
        # On/off switch for _run_auto_tracking, read/written directly by
        # main.py's GET/POST /api/autotracking — never used outside a chat
        # turn, so it lives here rather than on a separate object.
        self.auto_tracking_enabled = True

        # Single-user prototype: serializes chat-turn processing across
        # both transports and against a concurrent reset/activate/upload/
        # delete (main.py's _activate_and_reset awaits this same lock).
        self.lock = asyncio.Lock()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _strip_timestamps(history: list[dict]) -> list[dict]:
        """`LLMProvider.generate` only knows {role, content} — timestamps are
        kept in the persisted conversation for /api/signals, not sent to the
        model during normal chat."""
        return [{"role": m["role"], "content": m["content"]} for m in history]

    @staticmethod
    def build_priming_messages(attachments: list) -> list[dict]:
        """Never-persisted turn carrying attachments as provider-neutral
        'attachment' blocks, rebuilt fresh on every call. Public: also
        passed into signals.py's compute_signals() as a callback."""
        if not attachments:
            return []
        return [
            {
                "role": "user",
                "content": [
                    {"type": "attachment", "filename": a.filename, "source": a.source}
                    for a in attachments
                ],
            },
            {"role": "assistant", "content": "Understood."},
        ]

    def get_messages(self, last_n: int | None = None) -> list[dict]:
        """For main.py's GET /api/messages — the view only ever talks to
        the service, never reaches into db.py directly."""
        return db.get_messages(last_n=last_n)

    def _current_state_payload(self) -> dict:
        return self._get_active_automaton().get_current_state_payload()

    async def _run_auto_tracking(self, pending_message: dict) -> tuple[bool, str | None, str | None]:
        """Computes signals and applies the first matching trigger, if
        auto-tracking is on — before the reply, so it's produced under the
        destination state's prompt. Returns (state_changed, new_state, triggered_action)."""
        if not self.auto_tracking_enabled:
            return False, None, None

        signals_list = await self.signals.compute(
            self._llm_provider, self.build_priming_messages, pending_message
        )
        signal_values = {s["name"]: s["value"] for s in signals_list}
        # Saved before trigger evaluation so a fired transition can reference
        # the exact snapshot id that caused it.
        snapshot_id = db.save_signal_snapshot(signal_values)

        automaton = self._get_active_automaton()
        from_state_key = automaton.get_current_state().key
        triggered_action = automaton.evaluate_triggers(signal_values)

        if triggered_action is None:
            return False, None, None

        action = automaton.apply_action(triggered_action)
        relevant_names = trigger_signal_names(action.trigger)
        relevant_values = {n: signal_values.get(n) for n in relevant_names}
        automaton.log_transition(from_state_key, action.target, action.name, "auto", relevant_values)
        db.save_transition(from_state_key, triggered_action, action.target, snapshot_id)

        return True, action.target, triggered_action

    async def process_turn(self, text: str, on_retry: OnRetry | None = None) -> ChatTurnResult:
        """Runs one chat turn end to end: auto-tracking, prompt building,
        the LLM call (with retry/backoff), and persistence — identical
        regardless of which transport called it. `on_retry`, if given, is
        awaited on each backoff tick (see llm_provider.generate_with_retry);
        a transport with no one to report progress to (a synchronous REST
        call) just omits it — retries still happen server-side, silently.

        Raises ChatServiceError (never blocks/queues) if another turn is
        already in progress — the caller decides how to report that."""
        if self.lock.locked():
            raise ChatServiceError("A chat reply is already being generated.", status_code=409)
        async with self.lock:
            return await self._process_turn_locked(text, on_retry)

    async def _process_turn_locked(self, text: str, on_retry: OnRetry | None) -> ChatTurnResult:
        automaton = self._get_active_automaton()
        if automaton.get_current_state().final:
            # Final states are terminal by design: no message the client
            # could have already queued should reach the model, no matter
            # how the state got here (manual button or auto-tracking).
            raise ChatServiceError("The conversation has ended in this state.", status_code=409)

        pending_message = {"role": "user", "content": text, "timestamp": self._now_iso()}

        state_changed, new_state_key, triggered_action = await self._run_auto_tracking(pending_message)

        state = automaton.get_current_state()
        if state.fixed_message:
            logger.warning("Translating fixed_message for state '%s'.", state.key)
            system_prompt = FIXED_MESSAGE_INSTRUCTIONS.format(fixed_message=state.fixed_message)
            # A pure translation task doesn't use contextual_prompt, so it
            # doesn't carry the attachments meant for it either.
            turn_attachments = []
        else:
            system_prompt = f"{state.contextual_prompt}\n\n{automaton.general_prompt}"
            turn_attachments = automaton.general_prompt_attachments + state.attachments

        priming_messages = self.build_priming_messages(turn_attachments)
        chat_history = priming_messages + self._strip_timestamps(
            db.get_messages() + [pending_message]
        )

        try:
            reply = await generate_with_retry(
                self._llm_provider, system_prompt, chat_history, on_retry=on_retry
            )
        except LLMProviderUnavailableError as exc:
            raise ChatServiceError(
                f"Service unavailable after {MAX_RETRIES} retries.", str(exc), status_code=500
            ) from exc
        except LLMProviderRateLimitedError as exc:
            logger.critical("LLM provider rate limit exceeded: %s", exc)
            raise ChatServiceError("The AI service rate limit was exceeded.", str(exc), status_code=500) from exc
        except LLMProviderError as exc:
            raise ChatServiceError("The AI service returned an error.", str(exc), status_code=500) from exc

        # Persisted only once the turn is fully successful — a failed
        # attempt above raises without ever calling save_message, so a
        # message pair is either both persisted, or neither.
        db.save_message("user", text)
        db.save_message("assistant", reply)
        return ChatTurnResult(
            reply=reply,
            state=self._current_state_payload(),
            state_changed=state_changed,
            new_state=new_state_key,
            triggered_action=triggered_action,
        )

    async def open_if_needed(self) -> None:
        """If the conversation is empty, generates and persists the opening
        message (same prompt-building as a normal chat turn). No-op if
        already non-empty or generation fails.

        Purely a persistence step: delivering it to the client is always
        GET /api/messages's job (called by the frontend right after
        whatever REST call triggered this), never a transport push — so it
        reaches the client the same way regardless of whether a chat
        websocket happens to be connected."""
        if not db.is_empty():
            return

        automaton = self._get_active_automaton()
        state = automaton.get_current_state()

        if state.fixed_message:
            system_prompt = FIXED_MESSAGE_INSTRUCTIONS.format(fixed_message=state.fixed_message)
            turn_attachments = []
        else:
            system_prompt = f"{state.contextual_prompt}\n\n{automaton.general_prompt}"
            turn_attachments = automaton.general_prompt_attachments + state.attachments

        priming_messages = self.build_priming_messages(turn_attachments)
        priming_messages.append({"role": "user", "content": "..."})

        try:
            reply = await generate_with_retry(self._llm_provider, system_prompt, priming_messages)
        except LLMProviderError as exc:
            logger.warning("Failed to generate the opening message: %s", exc)
            return

        db.save_message("assistant", reply)
