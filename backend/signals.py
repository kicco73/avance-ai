"""Signals: computing and reporting the independent monitoring signals
defined in the active model's YAML (see `Signal` in automaton.py).

Same principle as db.py (persistence) and models_manager.py (model
lifecycle): one module, one responsibility. main.py calls compute_signals()
(from the auto-tracking flow) and get_latest_signals() (for GET
/api/signals) — it doesn't build the batch AI prompt, parse the reply, or
shape the response payload itself.

Boundary with automaton.py: this module produces a plain {signal_name:
value} dict; automaton.py's evaluate_triggers()/preview_triggers() are the
ones that consume it to decide whether an action fires. Trigger evaluation
(simpleeval, FIFO priority) stays entirely in automaton.py — it's state-
transition logic, not signals logic, even though it reads this module's
output.

Boundary with main.py: this module doesn't own the chat session (history,
current_state) or the shared LLM provider instance — both stay main.py's,
passed in explicitly (`history`, `llm_provider`, `build_priming_messages`)
rather than imported, the same way models_manager.py takes a `commit`
callback instead of reaching into main.py's session/chat_lock itself.
Persistence goes through db.py's save_signal_snapshot()/
get_latest_signal_snapshot(), never through peewee/playhouse directly.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

import db
import models_manager
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
# automaton.Attachment — supplied by main.py (see module docstring).
BuildPrimingMessages = Callable[[list], list[dict]]


def _signal_history_window(history: list[dict]) -> list[dict]:
    """Recent messages for a signals computation call, as a single
    'evaluate this transcript' user turn rather than multi-turn history:
    passing the raw conversation invites the model to keep chatting
    (roleplay another turn) instead of just scoring it. Still just a plain
    list of {role, content} — the LLMProvider interface itself is
    unaffected."""
    recent = history[-SIGNALS_HISTORY_WINDOW:]
    if recent and recent[0]["role"] != "user":
        recent = recent[1:]
    transcript = "\n".join(f"[{m['timestamp']}] {m['role']}: {m['content']}" for m in recent)
    return [{"role": "user", "content": f"Conversation transcript:\n\n{transcript}"}]


def _signals_payload(*, error: bool) -> list[dict]:
    return [
        {
            "name": s.name,
            "ui_label": s.ui_label,
            "description": s.description,
            "value": None,
            "error": error,
        }
        for s in models_manager.get_active_automaton().signals
    ]


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


def _validate_signal_value(raw_value: object) -> tuple[int | None, bool]:
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        return None, True
    if raw_value < 0 or raw_value > 100:
        return None, True
    return raw_value, False


async def compute_signals(
    llm_provider: LLMProvider,
    history: list[dict],
    build_priming_messages: BuildPrimingMessages,
) -> list[dict]:
    """Calls the AI to (re)compute all signal values for the active model
    from `history` (main.py's session.history). Only ever called from
    main.py's auto-tracking flow — never by GET /api/signals, which just
    reads the latest persisted snapshot instead of recalculating on every UI
    open. Always called with a non-empty `history` (a user message was just
    appended before this runs).

    `llm_provider` and `build_priming_messages` are supplied by the caller
    rather than imported: the provider is main.py's single shared instance,
    and priming-message construction is a generic attachments-to-turn
    helper shared with the chat path, not signals-specific logic."""
    automaton = models_manager.get_active_automaton()
    signal_definitions = "\n\n".join(
        f'Signal "{s.name}":\n{s.ai_prompt}' for s in automaton.signals
    )
    system_prompt = SIGNALS_SYSTEM_PROMPT_TEMPLATE.format(signal_definitions=signal_definitions)
    # Each signal brings only its own attachments into this shared call —
    # never a state's or general_prompt's (different scope entirely).
    signal_attachments = [a for s in automaton.signals for a in s.attachments]
    priming_messages = build_priming_messages(signal_attachments)
    call_history = priming_messages + _signal_history_window(history)

    try:
        raw_reply = await asyncio.to_thread(llm_provider.generate, system_prompt, call_history)
        parsed = _parse_signals_reply(raw_reply)
    except (LLMProviderError, json.JSONDecodeError, ValueError) as exc:
        logger.error("Failed to compute signals: %s", exc)
        return _signals_payload(error=True)

    results = []
    for s in automaton.signals:
        value, error = _validate_signal_value(parsed.get(s.name))
        results.append({
            "name": s.name,
            "ui_label": s.ui_label,
            "description": s.description,
            "value": value,
            "error": error,
        })
    return results


def _snapshot_to_signals_payload(snapshot: dict | None) -> list[dict]:
    """Builds the GET /api/signals response from a persisted snapshot (or
    None if one has never been computed yet). Values in a real snapshot were
    already validated by _validate_signal_value at save time; a signal
    missing from it (or explicitly null) means that specific signal's
    computation failed — distinct from no snapshot existing at all, which
    just means auto-tracking hasn't run yet."""
    results = []
    for s in models_manager.get_active_automaton().signals:
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


def get_latest_signals() -> list[dict]:
    """For GET /api/signals: read-only, never calls the AI. Signals are
    only (re)computed via compute_signals(), from main.py's auto-tracking
    flow; this just reports the latest snapshot persisted through db.py."""
    return _snapshot_to_signals_payload(db.get_latest_signal_snapshot())
