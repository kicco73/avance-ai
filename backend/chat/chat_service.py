"""Transport-agnostic chat-turn logic. Building the
system prompt, running auto-tracking, calling the LLM provider with
retry, and persisting the result all live here exactly once.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from http import HTTPStatus

from automaton.automaton import Action, Automaton, State, trigger_signal_names
from db import Db
from ai.ai_service import AiService, OnRetry
from chat.signals import Signals
from model_service import ModelService

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

# The model is prompted (see the active model's index.yml) to end its
# reply with a short [audio]...[/audio] tag — the phrase to narrate,
# distinct from the reply text itself. The tagged block (tag and content)
# is stripped from what gets persisted/shown; the content between the
# tags is what's sent to TTS instead of the reply text.
_AUDIO_TAG_RE = re.compile(r"\[audio\](.*?)\[/audio\]", re.IGNORECASE | re.DOTALL)


def _extract_audio_tag(text: str) -> tuple[str, str | None]:
    """Splits the model's raw reply into (visible_text, audio_text).
    visible_text has the [audio]...[/audio] block removed entirely — the
    persisted/displayed message never contains it. audio_text is what was
    inside it, or None if the model didn't include one at all (nothing to
    narrate for that message)."""
    match = _AUDIO_TAG_RE.search(text)
    if match is None:
        return text, None
    visible_text = (text[: match.start()] + text[match.end() :]).strip()
    return visible_text, match.group(1).strip()


class ChatServiceError(Exception):

    def __init__(
        self, message: str, detail: str | None = None, *, status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.status_code = status_code

class ChatService(object):
    def __init__(
        self,
        ai_service: AiService,
        model_service: ModelService,
        db: Db,
    ) -> None:
        self._ai_service = ai_service
        self._model_service = model_service
        self._db = db
        self.signals = Signals(
            get_active_automaton=lambda: model_service.get_active_automaton_and_state()[0], db=db
        )
        self.auto_tracking_enabled = True

        # Single-user prototype: serializes chat-turn processing across
        # both transports and against a concurrent reset/activate/upload/
        # delete (main.py's _activate_and_reset awaits this same lock).
        self.lock = asyncio.Lock()

    @property
    def _active_model_name(self) -> str:
        return self._model_service.get_active_model_name()

    def get_message_audio_text(self, message_id: int) -> str | None:
        return self._db.get_message_audio_text(message_id)

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

    async def get_messages(self, last_n: int | None = None) -> list[dict]:
        await self.open_if_needed()
        return self._db.get_messages(self._active_model_name, last_n=last_n)

    async def open_if_needed(self) -> None:
        # Generates the model's unprompted opening message if the current
        # state has had none yet — see _generate_opening_message_if_needed.
        model_name = self._active_model_name
        automaton, state = self._model_service.get_active_automaton_and_state()
        await self._generate_opening_message_if_needed(model_name, automaton, state)

    @staticmethod
    def _current_state_payload(automaton: Automaton, state: State) -> dict:
        return automaton.get_state_payload(state)

    def _history_cutoff(self, model_name: str, state: State) -> datetime | None:
        """Messages at or before this timestamp must be excluded from both
        the AI reply and auto-tracking's signal evaluation, per `state`'s
        clear_context. None means "no cutoff, use the full history"."""
        if not state.clear_context:
            return None
        return self._db.get_last_transition_timestamp(model_name)

    @staticmethod
    def _build_turn_prompt(automaton: Automaton, state) -> tuple[str, list]:
        # Shared by a normal chat turn and open_if_needed: system prompt +
        # attachments for a turn landing on `state`.
        if state.fixed_message:
            logger.warning("Translating fixed_message for state '%s'.", state.key)
            # A pure translation task doesn't use contextual_prompt, so it
            # doesn't carry the attachments meant for it either.
            return FIXED_MESSAGE_INSTRUCTIONS.format(fixed_message=state.fixed_message), []
        system_prompt = f"{state.contextual_prompt}\n\n{automaton.general_prompt}"
        return system_prompt, automaton.general_prompt_attachments + state.attachments

    async def _generate_opening_message_if_needed(
        self, model_name: str, automaton: Automaton, state: State
    ) -> dict | None:
        content_since = self._history_cutoff(model_name, state)
        chat_blocked = state.final or not state.chat
        gate_since = self._db.get_last_transition_timestamp(model_name) if chat_blocked else content_since
        if self._db.has_messages_since(model_name, gate_since):
            return None

        system_prompt, turn_attachments = self._build_turn_prompt(automaton, state)
        chat_history = (
            self.build_priming_messages(turn_attachments)
            + self._strip_timestamps(self._db.get_messages(model_name, since=content_since))
            + [{"role": "user", "content": "..."}]
        )

        reply = await self._ai_service.generate(system_prompt, chat_history)
        visible_text, audio_text = _extract_audio_tag(reply)
        message_id = self._db.save_message("assistant", visible_text, model_name, audio_text=audio_text)
        return {"id": message_id, "content": visible_text}

    async def _run_auto_tracking(
        self, pending_message: dict | None, model_name: str, automaton: Automaton, state: State
    ) -> tuple[Action | None, State, dict | None]:
        # Always returns the resulting state alongside the Action that fired
        # (None if nothing did) — callers never need to re-derive it via
        # action.target themselves. Third element: a proactive opening
        # message for the destination state, if a transition fired and
        # landed on a state with nothing since its own cutoff yet.
        # Skipped when auto-tracking is off globally, or when `state` has
        # no triggerable action at all — nothing an auto-tracking pass
        # could ever act on, so skip the signals call outright regardless
        # of the global flag (manual-only actions are unaffected either
        # way: they're never evaluated here, only via apply_manual_action).
        if not self.auto_tracking_enabled or not state.has_triggerable_actions:
            return None, state, None

        since = self._history_cutoff(model_name, state)
        signals_list = await self.signals.compute(
            self._ai_service, self.build_priming_messages, pending_message, since=since
        )
        signal_values = {s["name"]: s["value"] for s in signals_list}
        # Saved before trigger evaluation so a fired transition can reference
        # the exact snapshot id that caused it.
        snapshot_id = self._db.save_signal_snapshot(signal_values, model_name)

        triggered_action = automaton.evaluate_triggers(state.key, signal_values)
        if triggered_action is None:
            return None, state, None

        action = automaton.move(state.key, triggered_action)
        relevant_names = trigger_signal_names(action.trigger)
        relevant_values = {n: signal_values.get(n) for n in relevant_names}
        self._db.save_transition(
            state.key,
            triggered_action,
            action.target,
            model_name,
            transition_log_level=automaton.get_state(action.target).transition_log_level,
            signal_snapshot_id=snapshot_id,
            signal_values=relevant_values,
        )

        new_state = automaton.get_state(action.target)
        proactive_message = await self._generate_opening_message_if_needed(model_name, automaton, new_state)
        return action, new_state, proactive_message

    async def apply_manual_action(self, action_name: str) -> dict:
        """Applies a manual (button) action and, same as an auto-tracking
        transition, generates the destination state's opening message
        right away if it hasn't said anything yet — ModelService's own
        apply_manual_action() only performs the transition; it has no way
        to call the LLM. Without this, a state entered via a button
        (rather than an auto-tracking transition) would never get to
        speak at all, since no future turn re-lands on it either."""
        if self.lock.locked():
            raise ChatServiceError("A chat reply is already being generated.", status_code=HTTPStatus.CONFLICT)
        async with self.lock:
            state_payload = self._model_service.apply_manual_action(action_name)
            model_name = self._active_model_name
            automaton, state = self._model_service.get_active_automaton_and_state()
            proactive_message = await self._generate_opening_message_if_needed(model_name, automaton, state)
            reply = [proactive_message] if proactive_message is not None else []
            return {
                "state": state_payload,
                "reply": reply,
            }

    async def process_turn(self, text: str, on_retry: OnRetry | None = None) -> dict:
        if self.lock.locked():
            raise ChatServiceError("A chat reply is already being generated.", status_code=HTTPStatus.CONFLICT)
        async with self.lock:
            return await self._process_turn_locked(text, on_retry)

    async def _process_turn_locked(self, text: str, on_retry: OnRetry | None) -> dict:
        automaton, state = self._model_service.get_active_automaton_and_state()

        if state.final:
            raise ChatServiceError("The conversation has ended in this state.", status_code=HTTPStatus.CONFLICT)
        if not state.chat:
            raise ChatServiceError(
                "This state doesn't accept messages; use an action instead.", status_code=HTTPStatus.CONFLICT
            )

        pending_message = {"role": "user", "content": text, "timestamp": self._now_iso()}

        # Only the LAST transition that fires this turn is reported back,
        # even if both auto-tracking phases below cause one. `messages`
        # collects every bubble this turn produces, in order: a phase-1
        # proactive opening message (if any), then the turn's own reply,
        # then a phase-2 one (if any) — see the returned dict's "reply".
        # Each entry is {"id": ..., "content": ...}, not a bare string:
        # the id is what GET /api/chat/messages/{id}/audio is keyed on.
        action: Action | None = None
        model_name = self._active_model_name
        messages: list[dict] = []

        # Phase 1: on the user's message, before the reply is generated —
        # so the reply is produced under the destination state's prompt.
        # Gated by the automaton (model-wide), not the current state.
        if automaton.autotracking_on_user_message:
            action, state, proactive_message = await self._run_auto_tracking(
                pending_message, model_name, automaton, state
            )
            if proactive_message is not None:
                messages.append(proactive_message)

        system_prompt, turn_attachments = self._build_turn_prompt(automaton, state)

        priming_messages = self.build_priming_messages(turn_attachments)
        since = self._history_cutoff(model_name, state)
        chat_history = priming_messages + self._strip_timestamps(
            self._db.get_messages(model_name, since=since) + [pending_message]
        )

        reply = await self._ai_service.generate(system_prompt, chat_history, on_retry=on_retry)
        visible_reply, audio_text = _extract_audio_tag(reply)
        self._db.save_message("user", text, model_name)
        assistant_id = self._db.save_message("assistant", visible_reply, model_name, audio_text=audio_text)
        messages.append({"id": assistant_id, "content": visible_reply})

        # Phase 2: on the now-persisted user+assistant messages, under
        # whichever state phase 1 left us in — no pending_message, the
        # turn's messages are already in the DB. Also gated by the
        # automaton, not the current state.
        if automaton.autotracking_on_ai_message:
            last_action, state, proactive_message = await self._run_auto_tracking(
                None, model_name, automaton, state
            )
            if last_action:
                action = last_action
            if proactive_message is not None:
                messages.append(proactive_message)

        return {
            "reply": messages,
            "state": self._current_state_payload(automaton, state),
            "state_changed": action is not None,
            "new_state": action.target if action else None,
            "triggered_action": action.name if action else None,
        }
