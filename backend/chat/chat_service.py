"""Transport-agnostic chat-turn logic. Building the
system prompt, running auto-tracking, calling the LLM provider with
retry, and persisting the result all live here exactly once.
"""
from __future__ import annotations

import asyncio
import json
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

EMBED_METADATA_PROMPT = """
Always add a [avance]...[/avance] tag at the end of every response.
    - Write the content inside it as a dictionary in JSON format.
        - put the audio string, using "audio" as the key and its value as the value.
            - the content of the audio value must be designed for text-to-speech, not for reading.
            - Assume the user cannot see the screen at all.
            - Never refer to anything written on screen.
            - Keep the audio always concise (ideally under 5 seconds), but never omit information required to solve the task.
        - put a key "signals" as a dictionary
            - put all of the using their name as the key and their value as the value.
"""

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
_AVANCE_TAG_RE = re.compile(r"\[avance\](.*?)\[/avance\]", re.IGNORECASE | re.DOTALL)


def _extract_visible_text_and_metadata(text: str) -> tuple[str, dict | None]:
    match = _AVANCE_TAG_RE.search(text)
    if match is None:
        return text, {}
    visible_text = (text[: match.start()] + text[match.end() :]).strip()
    tag_text = match.group(1).strip()
    try:
        metadata = json.loads(tag_text) or {}
        assert isinstance(metadata, dict) 
    except Exception as exc:
        logger.warning(f"_extract_visible_text_and_metadata(): f{exc}")
        metadata = {}
    return visible_text, metadata


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
        # init_action's own message (if any) is deliberately never
        # persisted (see open_if_needed) — the only place it's surfaced.
        init_message = await self.open_if_needed()
        messages = self._db.get_messages(self._active_model_name, last_n=last_n)
        if init_message is not None:
            messages.insert(0, init_message)
        return messages

    async def open_if_needed(self) -> dict | None:
        """The one place init_action gets resolved: unconditional, and
        entirely separate from the trigger/auto-tracking loop (see
        Automaton.init_action) — model_service.get_active_automaton_and_state
        already falls back to init_action.target on its own for every
        caller, so this only has to notice the *first* such fallback
        (db.get_current_state still None) and make it stick by persisting
        the transition, plus fire init_action's own action_prompt message
        exactly once. Then runs the existing opening-message check on
        whatever state that leaves us in."""
        model_name = self._active_model_name
        automaton, state = self._model_service.get_active_automaton_and_state()

        init_message = None
        if self._db.get_current_state(model_name) is None:
            action = automaton.init_action
            self._db.save_transition(
                "", action.name, state.key, model_name, transition_log_level=state.transition_log_level
            )
            if action.action_prompt:
                init_message = await self._generate_action_prompt_message(action, model_name, automaton, state)

        await self._generate_opening_message_if_needed(model_name, automaton, state)
        return init_message

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

    def _build_turn_prompt(self, automaton, state) -> tuple[str, list]:
        # Shared by a normal chat turn and open_if_needed: system prompt +
        # attachments for a turn landing on `state`.
        if state.fixed_message:
            logger.warning("Translating fixed_message for state '%s'.", state.key)
            # A pure translation task doesn't use contextual_prompt, so it
            # doesn't carry the attachments meant for it either.
            return FIXED_MESSAGE_INSTRUCTIONS.format(fixed_message=state.fixed_message), []

        metadata_prompt = self.signals.get_definition() + '\n' + EMBED_METADATA_PROMPT
        system_prompt = f"{state.contextual_prompt}\n\n{automaton.general_prompt}\n\n{metadata_prompt}"
        return system_prompt, automaton.general_prompt_attachments + state.attachments

    def _should_generate_opening_message(self, model_name: str, state: State) -> bool:
        # Evaluated BEFORE any message this same transition adds is
        # persisted (see _messages_for_transition) — an action_prompt
        # message must never make this gate think `state` already spoke.
        content_since = self._history_cutoff(model_name, state)
        chat_blocked = state.final or not state.chat
        gate_since = self._db.get_last_transition_timestamp(model_name) if chat_blocked else content_since
        return not self._db.has_messages_since(model_name, gate_since)

    async def _generate_opening_message_if_needed(
        self, model_name: str, automaton: Automaton, state: State
    ) -> dict | None:
        if not self._should_generate_opening_message(model_name, state):
            return None
        return await self._generate_opening_message_body(model_name, automaton, state)

    async def _generate_opening_message_body(
        self, model_name: str, automaton: Automaton, state: State
    ) -> dict:
        content_since = self._history_cutoff(model_name, state)
        system_prompt, turn_attachments = self._build_turn_prompt(automaton, state)
        chat_history = (
            self.build_priming_messages(turn_attachments)
            + self._strip_timestamps(self._db.get_messages(model_name, since=content_since))
            + [{"role": "user", "content": "..."}]
        )

        reply = await self._ai_service.generate(system_prompt, chat_history)
        visible_text, metadata = _extract_visible_text_and_metadata(reply)
        audio_text = metadata.get('audio')
        message_id = self._db.save_message("assistant", visible_text, model_name, audio_text=audio_text)
        return {"id": message_id, "content": visible_text, "audio_text": audio_text}

    async def _generate_action_prompt_message(
        self, action: Action, model_name: str, automaton: Automaton, state: State
    ) -> dict:
        logger.warning("Executing action_prompt for action '%s'.", action.name)

        system_prompt = automaton.general_prompt
        turn_attachments = automaton.general_prompt_attachments + state.attachments

        since = self._history_cutoff(model_name, state)
        chat_history = (
            self.build_priming_messages(turn_attachments)
            + self._strip_timestamps(self._db.get_messages(model_name, since=since))
            + [{"role": "user", "content": action.action_prompt}]
        )

        reply = await self._ai_service.generate(system_prompt, chat_history)
        visible_text, avance_tag_content = _extract_visible_text_and_metadata(reply)
        return {"id": None, "content": visible_text, "metadata": avance_tag_content}

    async def _messages_for_transition(
        self, action: Action, model_name: str, automaton: Automaton, new_state: State, *, is_self_loop: bool
    ) -> list[dict]:
        # Shared by the auto-tracking and manual-action paths: action_prompt
        # (if set) always generates first, then the destination state's own
        # opening/fixed_message (existing, gated mechanism) — independent of
        # each other, so both can land for the same transition. Eligibility
        # for the opening message is decided BEFORE action_prompt's message
        # is persisted, so it never sees that message as "already spoken".
        # A self-loop (action.target == the state it fired from) must have
        # no side effect beyond action_prompt — it re-enters a state that
        # was never really left, not a genuine new entry to open.
        should_open = not is_self_loop and self._should_generate_opening_message(model_name, new_state)

        messages = []
        if action.action_prompt:
            messages.append(
                await self._generate_action_prompt_message(action, model_name, automaton, new_state)
            )
        if should_open:
            messages.append(await self._generate_opening_message_body(model_name, automaton, new_state))
        return messages

    async def _run_auto_tracking(
        self, pending_message: dict | None, model_name: str, automaton: Automaton, state: State, signal_values: list[str] | None
    ) -> tuple[Action | None, State, list[dict]]:
        if not self.auto_tracking_enabled or not state.has_triggerable_actions:
            return None, state, []

        if not signal_values: 
            # fallback, we need to call AI to compute values
            logger.warning(f'_run_auto_tracking(): signals not found in metadata, falling back to AI')
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
            return None, state, []

        action = automaton.move(state.key, triggered_action)
        relevant_names = trigger_signal_names(action.trigger)
        relevant_values = {n: signal_values.get(n) for n in relevant_names}
        # A self-loop isn't a real transition — skip persisting it (see
        # ModelService.apply_manual_action for why: it would otherwise
        # bump the clear_context cutoff for no actual state change).
        if action.target != state.key:
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
        messages = await self._messages_for_transition(
            action, model_name, automaton, new_state, is_self_loop=(action.target == state.key)
        )
        return action, new_state, messages

    async def apply_manual_action(self, action_name: str) -> dict:
        """Applies a manual (button) action and, same as an auto-tracking
        transition, generates its action_prompt message (if any) and the
        destination state's opening message if it hasn't spoken yet —
        ModelService's own apply_manual_action() only performs the
        transition; it has no way to call the LLM."""
        if self.lock.locked():
            raise ChatServiceError("A chat reply is already being generated.", status_code=HTTPStatus.CONFLICT)
        async with self.lock:
            state_payload, action, source_state_key = self._model_service.apply_manual_action(action_name)
            model_name = self._active_model_name
            automaton, state = self._model_service.get_active_automaton_and_state()
            reply = await self._messages_for_transition(
                action, model_name, automaton, state, is_self_loop=(action.target == source_state_key)
            )
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

        action: Action | None = None
        model_name = self._active_model_name
        messages: list[dict] = []

        if automaton.autotracking_on_user_message:
            action, state, transition_messages = await self._run_auto_tracking(
                pending_message, model_name, automaton, state, {}
            )
            messages.extend(transition_messages)

        system_prompt, turn_attachments = self._build_turn_prompt(automaton, state)

        priming_messages = self.build_priming_messages(turn_attachments)
        since = self._history_cutoff(model_name, state)
        chat_history = priming_messages + self._strip_timestamps(
            self._db.get_messages(model_name, since=since) + [pending_message]
        )

        reply = await self._ai_service.generate(system_prompt, chat_history, on_retry=on_retry)
        visible_reply, metadata = _extract_visible_text_and_metadata(reply)
        audio_text = metadata.get('audio')
        self._db.save_message("user", text, model_name)
        assistant_id = self._db.save_message("assistant", visible_reply, model_name, audio_text=audio_text)
        messages.append({"id": assistant_id, "content": visible_reply, "audio_text": audio_text})

        if automaton.autotracking_on_ai_message:
            last_action, state, transition_messages = await self._run_auto_tracking(
                None, model_name, automaton, state, metadata.get('signals')
            )
            if last_action:
                action = last_action
            messages.extend(transition_messages)

        return {
            "reply": messages,
            "state": self._current_state_payload(automaton, state),
            "state_changed": action is not None,
            "new_state": action.target if action else None,
            "triggered_action": action.name if action else None,
        }
