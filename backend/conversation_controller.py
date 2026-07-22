"""Owns the /ws/chat connection, turning messages into persisted (user,
assistant) pairs and opening the conversation when empty. Doesn't own
which automaton is active — that's constructor-injected from main.py.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

from automaton import Automaton, trigger_signal_names
from db import db
from llm_provider import (
    LLMProvider,
    LLMProviderError,
    LLMProviderRateLimitedError,
    LLMProviderUnavailableError,
    MAX_RETRIES,
    generate_with_retry,
)
from signals import signals

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


Send = Callable[[dict], Awaitable[None]]

# Supplies the currently-active Automaton (models_manager.get_active_automaton)
# — passed in rather than imported: this module doesn't own which model is
# active, the same reason models_manager.py itself doesn't import main.py.
GetActiveAutomaton = Callable[[], Automaton]


class ConversationController(object):
    def __init__(
        self,
        llm_provider: LLMProvider,
        get_active_automaton: GetActiveAutomaton,
    ) -> None:
        self._llm_provider = llm_provider
        self._get_active_automaton = get_active_automaton
        # On/off switch for _run_auto_tracking, read/written directly by
        # main.py's GET/POST /api/autotracking — never used outside a chat
        # turn, so it lives on the controller rather than a separate object.
        self.auto_tracking_enabled = True

        # Single-user prototype: serializes chat-turn processing against a
        # concurrent reset/activate/upload/delete. Held around a whole chat
        # turn (chat_loop below) or a whole reset (main.py's _activate_and_reset).
        self.lock = asyncio.Lock()
        # At most one /ws/chat connection is ever live at a time in this
        # single-user prototype — no set of connections to track, just
        # whichever one (if any) is currently open.
        self._websocket: WebSocket | None = None

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
        the controller, never reaches into db.py directly."""
        return db.get_messages(last_n=last_n)

    def _current_state_payload(self) -> dict:
        return self._get_active_automaton().get_current_state_payload()

    async def _run_auto_tracking(self, pending_message: dict) -> tuple[bool, str | None, str | None]:
        """Computes signals and applies the first matching trigger, if
        auto-tracking is on — before the reply, so it's produced under the
        destination state's prompt. Returns (state_changed, new_state, triggered_action)."""
        if not self.auto_tracking_enabled:
            return False, None, None

        signals_list = await signals.compute_signals(
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

    async def chat_loop(self, websocket: WebSocket) -> None:
        """Accepts the /ws/chat connection and dispatches every non-empty
        frame to process_message(), serialized by `lock`. Busy is reported
        back over the socket rather than queued."""
        await websocket.accept()
        self._websocket = websocket
        try:
            while True:
                data = await websocket.receive_json()
                text = (data or {}).get("message", "").strip()
                if not text:
                    continue
                if self.lock.locked():
                    await websocket.send_json({
                        "type": "error",
                        "error": "A chat reply is already being generated.",
                    })
                    continue
                async with self.lock:
                    await self.process_message(text, websocket.send_json)
        except WebSocketDisconnect:
            pass
        finally:
            if self._websocket is websocket:
                self._websocket = None

    async def push(self, payload: dict) -> None:
        """Sends `payload` to the open /ws/chat connection, if any — a
        no-op otherwise, since whatever triggered this already persisted
        its result via db.save_message()."""
        if self._websocket is None:
            return
        try:
            await self._websocket.send_json(payload)
        except Exception:
            pass  # a dropped connection is cleaned up by chat_loop's own finally

    async def process_message(self, text: str, send: Send) -> None:
        """Runs one chat turn, pushing status updates via `send`. The
        user's message isn't persisted until the turn fully succeeds —
        kept only as local `pending_message` until then."""
        automaton = self._get_active_automaton()
        if automaton.get_current_state().final:
            # Final states are terminal by design: no message the client
            # could have already queued should reach the model, no matter
            # how the state got here (manual button or auto-tracking).
            await send({"type": "failed", "error": "The conversation has ended in this state."})
            return

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
        transition_fields = {
            "state_changed": state_changed,
            "new_state": new_state_key,
            "triggered_action": triggered_action,
        }

        async def _push_retrying(attempt: int, max_attempts: int, retry_in: float) -> None:
            await send({
                "type": "retrying",
                "attempt": attempt,
                "max_attempts": max_attempts,
                "retry_in": retry_in,
            })

        try:
            reply = await generate_with_retry(
                self._llm_provider, system_prompt, chat_history, on_retry=_push_retrying
            )
        except LLMProviderUnavailableError as exc:
            await send({
                "type": "failed",
                "error": f"Service unavailable after {MAX_RETRIES} retries: {exc}",
                **transition_fields,
            })
            return
        except LLMProviderRateLimitedError as exc:
            logger.critical("LLM provider rate limit exceeded: %s", exc)
            await send({"type": "failed", "error": str(exc), **transition_fields})
            return
        except LLMProviderError as exc:
            await send({"type": "failed", "error": str(exc), **transition_fields})
            return

        # Persisted only once the turn is fully successful — a failed
        # attempt above returns without ever calling save_message, so a
        # message pair is either both persisted, or neither.
        db.save_message("user", text)
        db.save_message("assistant", reply)
        await send({
            "type": "done",
            "reply": reply,
            "state": self._current_state_payload(),
            **transition_fields,
        })

    async def open_if_needed(self) -> None:
        """If the conversation is empty, generates and persists the opening
        message (same prompt-building as a normal chat turn) and pushes it
        to the open connection. No-op if already non-empty or generation fails."""
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
        await self.push({
            "type": "message",
            "reply": reply,
            "state": self._current_state_payload(),
        })
