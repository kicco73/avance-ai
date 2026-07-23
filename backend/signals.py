"""Computes and reports the monitoring signals defined in the active
model's YAML. `Signals` holds no real state — get_active_automaton is
constructor-injected. Instantiated as ConversationController's `signals`."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

from automaton import Automaton
from db import db
from llm_provider import LLMProvider, LLMProviderError

logger = logging.getLogger(__name__)

# How many recent history messages to send the model for a signals
# computation call. Kept even so a slice always starts on a "user" turn
# (history strictly alternates user/assistant, in pairs).
SIGNALS_HISTORY_WINDOW = 14

SIGNALS_SYSTEM_PROMPT_TEMPLATE = (
    "You are evaluating a conversation for a set of independent monitoring "
    "signals. Each conversation message below is prefixed with its ISO 8601 "
    "timestamp. Evaluate each signal independently and only from what was "
    "actually said in this excerpt.\n\n"
    "{signal_definitions}\n\n"
    "Respond with ONLY a single JSON object mapping each signal name above to "
    'its integer value from 0 to 100, in this exact form: {{"signal_name": '
    "value, ...}}. Include every signal listed above, nothing else — no "
    "explanation, no markdown formatting, just the JSON object."
)

# The shape compute_signals() needs to build a priming turn from a list of
# automaton.Attachment — supplied by the caller (see module docstring).
BuildPrimingMessages = Callable[[list], list[dict]]

# Supplies the currently-active Automaton — constructor-injected rather
# than imported: this module doesn't own which model is active.
GetActiveAutomaton = Callable[[], Automaton]


class Signals(object):
    def __init__(self, get_active_automaton: GetActiveAutomaton) -> None:
        self._get_active_automaton = get_active_automaton

    @property
    def automaton(self) -> Automaton:
        return self._get_active_automaton()

    @staticmethod
    def _active_model_name() -> str:
        """Settings-persisted pointer, not models_manager's in-memory
        tracker — this module doesn't depend on models_manager, by design."""
        return db.get_active_model_name()

    @staticmethod
    def _signal_history_window(pending_message: dict | None) -> list[dict]:
        """Recent messages as a single 'evaluate this transcript' turn —
        not multi-turn history, which invites the model to keep chatting.
        `pending_message` is appended locally, unpersisted."""
        fetch_n = SIGNALS_HISTORY_WINDOW - 1 if pending_message is not None else SIGNALS_HISTORY_WINDOW
        recent = db.get_messages(Signals._active_model_name(), last_n=fetch_n)
        if pending_message is not None:
            recent = recent + [pending_message]
        if recent and recent[0]["role"] != "user":
            recent = recent[1:]
        transcript = "\n".join(f"[{m['timestamp']}] {m['role']}: {m['content']}" for m in recent)
        return [{"role": "user", "content": f"Conversation transcript:\n\n{transcript}"}]

    @staticmethod
    def _parse_signals_reply(raw_reply: str) -> dict:
        text = raw_reply.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                first_line, rest = text.split("\n", 1)
                if first_line.strip().isalpha():
                    text = rest
            text = text.strip()
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("Signals response is not a JSON object.")
        return parsed

    @staticmethod
    def _validate_signal_value(raw_value: object) -> tuple[int | None, bool]:
        if isinstance(raw_value, bool) or not isinstance(raw_value, int):
            return None, True
        if raw_value < 0 or raw_value > 100:
            return None, True
        return raw_value, False

    def _signals_payload(self, *, error: bool) -> list[dict]:
        return [
            {
                "name": s.name,
                "ui_label": s.ui_label,
                "description": s.description,
                "value": None,
                "error": error,
            }
            for s in self.automaton.signals
        ]

    async def compute(
        self,
        llm_provider: LLMProvider,
        build_priming_messages: BuildPrimingMessages,
        pending_message: dict | None = None,
    ) -> list[dict]:
        """Calls the AI to (re)compute signal values from the persisted
        conversation plus `pending_message` (evaluated even though not yet
        persisted). Only called from the auto-tracking flow."""
        automaton = self.automaton
        signal_definitions = "\n\n".join(
            f'Signal "{s.name}":\n{s.ai_prompt}' for s in automaton.signals
        )
        system_prompt = SIGNALS_SYSTEM_PROMPT_TEMPLATE.format(signal_definitions=signal_definitions)
        # Each signal brings only its own attachments into this shared call —
        # never a state's or general_prompt's (different scope entirely).
        signal_attachments = [a for s in automaton.signals for a in s.attachments]
        priming_messages = build_priming_messages(signal_attachments)
        call_history = priming_messages + self._signal_history_window(pending_message)

        try:
            raw_reply = await asyncio.to_thread(llm_provider.generate, system_prompt, call_history)
            parsed = self._parse_signals_reply(raw_reply)
        except (LLMProviderError, json.JSONDecodeError, ValueError) as exc:
            logger.error("Failed to compute signals: %s", exc)
            return self._signals_payload(error=True)

        results = []
        for s in automaton.signals:
            value, error = self._validate_signal_value(parsed.get(s.name))
            results.append({
                "name": s.name,
                "ui_label": s.ui_label,
                "description": s.description,
                "value": value,
                "error": error,
            })
        return results

    def _snapshot_to_signals_payload(self, snapshot: dict | None) -> list[dict]:
        """Builds the GET /api/signals response from a persisted snapshot
        (or None). A missing/null value means that signal's computation
        failed — distinct from no snapshot at all (auto-tracking hasn't run)."""
        results = []
        for s in self.automaton.signals:
            if snapshot is None:
                value, error = None, False
            else:
                value = snapshot.get(s.name)
                error = value is None
            results.append({
                "name": s.name,
                "ui_label": s.ui_label,
                "description": s.description,
                "value": value,
                "error": error,
            })
        return results

    def get_latest_signals(self) -> list[dict]:
        """Read-only, never calls the AI — reports the latest snapshot
        persisted through db.py. Signals are only (re)computed via
        compute_signals(), from the auto-tracking flow."""
        return self._snapshot_to_signals_payload(db.get_latest_signal_snapshot(self._active_model_name()))
