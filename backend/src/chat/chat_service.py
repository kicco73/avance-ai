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
from typing import Awaitable, Callable

from automaton.automaton import Action, Automaton, State, StatePayload
from db import Db
from ai.ai_service import AiService, OnRetry
from chat.auto_tracker import AutoTracker
from chat.metadata_handler import MetadataHandler
from chat.priming import build_priming_messages
from chat.signals import Signals
from project.project_service import ProjectService

logger = logging.getLogger(__name__)

OnChunk = Callable[[str], Awaitable[None]]

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

# The AI is prompted (see the active project's index.yml) to end its
# reply with a short [avance]...[/avance] tag — the phrase to narrate,
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
        logger.warning(f"_extract_visible_text_and_metadata(): {exc}")
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
        project_service: ProjectService,
        db: Db,
    ) -> None:
        self._ai_service = ai_service
        self._project_service = project_service
        self._db = db
        self.signals = Signals(
            get_active_automaton=lambda: project_service.get_active_automaton_and_state()[0], db=db
        )
        self._metadata_handler = MetadataHandler()
        self._auto_tracker = AutoTracker(db, ai_service, self.signals)
        self.auto_tracking_enabled = True

        # Single-user prototype: serializes chat-turn processing across
        # both transports and against a concurrent reset/activate/upload/
        # delete (main.py's _activate_and_reset awaits this same lock).
        self.lock = asyncio.Lock()

    @property
    def _active_project_name(self) -> str:
        return self._project_service.get_active_project_name()

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

    async def get_messages(self, last_n: int | None = None) -> list[dict]:
        # init_action's own message (if any) is deliberately never
        # persisted (see open_if_needed) — the only place it's surfaced.
        init_message = await self.open_if_needed()
        messages = self._db.get_messages(self._active_project_name, last_n=last_n)
        if init_message is not None:
            messages.insert(0, init_message)
        return messages

    async def open_if_needed(self) -> dict | None:
        project_name = self._active_project_name
        automaton, state = self._project_service.get_active_automaton_and_state()

        init_message = None
        if self._db.get_current_state(project_name) is None:
            action = automaton.init_action
            self._db.save_transition(
                "", action.name, state.key, project_name, transition_log_level=state.transition_log_level
            )
            if action.action_prompt:
                init_message = await self._generate_action_prompt_message(action, project_name, automaton, state)

        await self._generate_opening_message_if_needed(project_name, automaton, state)
        return init_message

    @staticmethod
    def _current_state_payload(automaton: Automaton, state: State) -> StatePayload:
        return automaton.get_state_payload(state)

    def _history_cutoff(self, project_name: str, state: State) -> datetime | None:
        """Messages at or before this timestamp must be excluded from both
        the AI reply and auto-tracking's signal evaluation, per `state`'s
        history_cutoff. None means "no cutoff, use the full history"."""
        if not state.history_cutoff:
            return None
        return self._db.get_last_transition_timestamp(project_name)

    def _build_turn_prompt(self, automaton: Automaton, state: State) -> tuple[str, list]:
        if state.fixed_message:
            logger.warning("Translating fixed_message for state '%s'.", state.key)
            return FIXED_MESSAGE_INSTRUCTIONS.format(fixed_message=state.fixed_message), []

        metadata_prompt = self._metadata_handler.build_prompt(self.signals.get_definition())
        system_prompt = f"{state.contextual_prompt}\n\n{automaton.general_prompt}\n\n{metadata_prompt}"
        return system_prompt, list(automaton.general_attachments.values()) + list(state.attachments.values())

    def _should_generate_opening_message(self, project_name: str, state: State) -> bool:
        content_since = self._history_cutoff(project_name, state)
        chat_blocked = state.final or not state.chat
        gate_since = self._db.get_last_transition_timestamp(project_name) if chat_blocked else content_since
        return not self._db.has_messages_since(project_name, gate_since)

    async def _generate_opening_message_if_needed(
        self, project_name: str, automaton: Automaton, state: State
    ) -> dict | None:
        if not self._should_generate_opening_message(project_name, state):
            return None
        return await self._generate_opening_message_body(project_name, automaton, state)

    async def _generate_opening_message_body(
        self, project_name: str, automaton: Automaton, state: State
    ) -> dict:
        content_since = self._history_cutoff(project_name, state)
        system_prompt, turn_attachments = self._build_turn_prompt(automaton, state)
        chat_history = (
            build_priming_messages(turn_attachments)
            + self._strip_timestamps(self._db.get_messages(project_name, since=content_since))
            + [{"role": "user", "content": "..."}]
        )

        reply = await self._ai_service.generate(system_prompt, chat_history)
        visible_text, metadata = _extract_visible_text_and_metadata(reply)
        audio_text = self._metadata_handler.audio_text(metadata)
        message_id = self._db.save_message("assistant", visible_text, project_name, audio_text=audio_text)
        return {"id": message_id, "content": visible_text, "audio_text": audio_text}

    async def _generate_action_prompt_message(
        self, action: Action, project_name: str, automaton: Automaton, state: State
    ) -> dict:
        logger.warning("Executing action_prompt for action '%s'.", action.name)

        system_prompt = automaton.general_prompt
        turn_attachments = list(automaton.general_attachments.values()) + list(state.attachments.values())

        since = self._history_cutoff(project_name, state)
        chat_history = (
            build_priming_messages(turn_attachments)
            + self._strip_timestamps(self._db.get_messages(project_name, since=since))
            + [{"role": "user", "content": action.action_prompt}]
        )

        reply = await self._ai_service.generate(system_prompt, chat_history)
        visible_text, metadata = _extract_visible_text_and_metadata(reply)
        return {"id": None, "content": visible_text, "audio_text": self._metadata_handler.audio_text(metadata)}

    async def _messages_for_transition(
        self, action: Action, project_name: str, automaton: Automaton, new_state: State, *, is_self_loop: bool
    ) -> list[dict]:
        should_open = not is_self_loop and self._should_generate_opening_message(project_name, new_state)

        messages = []
        if action.action_prompt:
            messages.append(
                await self._generate_action_prompt_message(action, project_name, automaton, new_state)
            )
        if should_open:
            messages.append(await self._generate_opening_message_body(project_name, automaton, new_state))
        return messages

    async def _run_auto_tracking(
        self, pending_message: dict | None, project_name: str, automaton: Automaton, state: State, signal_values: dict | None
    ) -> tuple[Action | None, State, list[dict]]:
        if not self.auto_tracking_enabled:
            return None, state, []

        action, new_state = await self._auto_tracker.run(pending_message, project_name, automaton, state, signal_values)
        if action is None:
            return None, state, []

        messages = await self._messages_for_transition(
            action, project_name, automaton, new_state, is_self_loop=(action.target == state.key)
        )
        return action, new_state, messages

    async def apply_manual_action(self, action_name: str) -> dict:
        if self.lock.locked():
            raise ChatServiceError("A chat reply is already being generated.", status_code=HTTPStatus.CONFLICT)
        async with self.lock:
            state_payload, action, source_state_key = self._project_service.apply_manual_action(action_name)
            project_name = self._active_project_name
            automaton, state = self._project_service.get_active_automaton_and_state()
            reply = await self._messages_for_transition(
                action, project_name, automaton, state, is_self_loop=(action.target == source_state_key)
            )
            return {
                "state": state_payload,
                "reply": reply,
            }

    async def process_turn(
        self, text: str, on_retry: OnRetry | None = None, on_chunk: OnChunk | None = None
    ) -> dict:
        if self.lock.locked():
            raise ChatServiceError("A chat reply is already being generated.", status_code=HTTPStatus.CONFLICT)
        async with self.lock:
            return await self._process_turn_locked(text, on_retry, on_chunk)

    async def _process_turn_locked(
        self, text: str, on_retry: OnRetry | None, on_chunk: OnChunk | None
    ) -> dict:
        automaton, state = self._project_service.get_active_automaton_and_state()

        if not state.chat:
            raise ChatServiceError(
                "This state doesn't accept messages; use an action instead.", status_code=HTTPStatus.CONFLICT
            )

        pending_message = {"role": "user", "content": text, "timestamp": self._now_iso()}

        action: Action | None = None
        project_name = self._active_project_name
        messages: list[dict] = []

        if automaton.autotracking_on_user_message:
            action, state, transition_messages = await self._run_auto_tracking(
                pending_message, project_name, automaton, state, {}
            )
            messages.extend(transition_messages)

        self._db.save_message("user", text, project_name)

        if state.chat:
            system_prompt, turn_attachments = self._build_turn_prompt(automaton, state)

            priming_messages = build_priming_messages(turn_attachments)
            since = self._history_cutoff(project_name, state)
            chat_history = priming_messages + self._strip_timestamps(self._db.get_messages(project_name, since=since))

            if on_chunk is not None:
                full_raw_reply = ""
                async for chunk in self._ai_service.generate_stream(system_prompt, chat_history):
                    full_raw_reply += chunk
                    await on_chunk(chunk)
                reply = full_raw_reply
            else:
                reply = await self._ai_service.generate(system_prompt, chat_history, on_retry=on_retry)

            visible_reply, metadata = _extract_visible_text_and_metadata(reply)
            audio_text = self._metadata_handler.audio_text(metadata)
            assistant_id = self._db.save_message("assistant", visible_reply, project_name, audio_text=audio_text)
            messages.append({"id": assistant_id, "content": visible_reply, "audio_text": audio_text})

            if automaton.autotracking_on_ai_message:
                last_action, state, transition_messages = await self._run_auto_tracking(
                    None, project_name, automaton, state, self._metadata_handler.signal_values(metadata)
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