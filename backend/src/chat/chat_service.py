from __future__ import annotations

import asyncio
import json
import logging

from datetime import datetime, timezone
from http import HTTPStatus
from typing import Awaitable, Callable, Any

from automaton.automaton import Action, Automaton, State, StatePayload
from db import Db
from ai.ai_service import AiService, OnRetry
from session import Session

from chat.auto_tracker import AutoTracker
from chat.metadata_handler import MetadataHandler
from chat.metrics_service import ChatMetrics
from chat.priming import build_priming_messages
from chat.session_manager import ChatSessionManager
from chat.signals import Signals
from project.project_service import ProjectService
from chat.text_filter import TagFilter, ConcatTagFilter

logger = logging.getLogger(__name__)

OnChunk = Callable[[str], Awaitable[None]]
OnAudio = Callable[[str], Awaitable[None]]

FIXED_MESSAGE_INSTRUCTIONS = (
    "You must reply with ONLY a translation of the fixed message below into "
    "the same language the user's last message is written in. Do not answer "
    "or react to what the user said, do not add or remove anything, and do "
    "not change its meaning or formatting — output just the translation.\n\n"
    "Fixed message:\n{fixed_message}"
)

def _parse_metadata_tag(metadata_tag: str) -> Any:
    metadata : dict[str, Any] = {}
    try:
        metadata  = json.loads(metadata_tag) or {}
        assert isinstance(metadata, dict)
    except Exception as exc:
        logger.warning(f"_parse_metadata_tag(): {exc}")  
    return metadata
    

def _filter_text_and_extract_tags(text: str) -> tuple[str, dict]:
    filters = ConcatTagFilter('audio', 'avance')
    return filters.filter_and_flush(text), {
        'audio': filters.tags['audio'].tag_content,
        'signals': _parse_metadata_tag(filters.tags['avance'].tag_content)   
    }

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
        session_manager: ChatSessionManager,
    ) -> None:
        self._ai_service = ai_service
        self._project_service = project_service
        self._db = db
        self._session_manager = session_manager
        self.signals = Signals(
            get_active_automaton=lambda: project_service.get_active_automaton_and_state()[0], db=db
        )
        self.metrics = ChatMetrics(
            db, get_username=lambda: Session().user, get_active_project_name=lambda: project_service.get_active_project_name()
        )
        self._metadata_handler = MetadataHandler()
        self._auto_tracker = AutoTracker(db, ai_service, self.signals, self.metrics)
        self.auto_tracking_enabled = True

        # Single-user prototype: serializes chat-turn processing across
        # both transports and against a concurrent reset/activate/upload/
        # delete (main.py's _activate_and_reset awaits this same lock).
        self.lock = asyncio.Lock()

    @property
    def _active_project_name(self) -> str:
        return self._project_service.get_active_project_name()

    @property
    def _username(self) -> str:
        return Session().user

    def get_message_audio_text(self, message_id: int) -> str | None:
        return self._db.get_message_audio_text(message_id)

    def get_ai_models_info(self) -> dict:
        return self._ai_service.get_models_info()

    def select_ai_model(self, index: int | None) -> None:
        self._ai_service.select_model(index)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _strip_timestamps(history: list[dict]) -> list[dict]:
        """`LLMProvider.generate` only knows {role, content} — timestamps are
        kept in the persisted conversation for /api/signals, not sent to the
        model during normal chat."""
        return [{"role": m["role"], "content": m["content"]} for m in history]

    def _session_payload(self, session: dict, *, active: bool) -> dict:
        return {
            "id": session["id"],
            "project_name": session["project_name"],
            "datetime_start": session["datetime_start"].isoformat(),
            "datetime_end": session["datetime_end"].isoformat(),
            "start_state": session["start_state"],
            "end_state": session["end_state"],
            "open": self._session_manager.is_open(session),
            # Distinct from "open" (see session_manager.py's module
            # docstring): the single open session with the most recent
            # datetime_start for this project — what the frontend must
            # trust to decide whether this session still accepts chat
            # turns/manual actions, never computed client-side.
            "active": active,
        }

    def _require_active_session(self, session_id: int | None, project_name: str, current_state: str) -> dict:
        """A chat turn's session must already be the active one for this
        project — never silently rotated to a different one, and rejected
        just as firmly if it's merely open-but-superseded as if it were
        outright closed (see session_manager.py's module docstring).
        ValueError becomes a 409 the frontend can act on, e.g. hiding
        manual action buttons and disabling the input until the user
        bootstraps/starts a new session (see ChatWindow.vue/chatStore.js)."""
        try:
            return self._session_manager.require_active_session(
                self._username, project_name, session_id, current_state
            )
        except ValueError as exc:
            raise ChatServiceError(str(exc), status_code=HTTPStatus.CONFLICT) from exc

    def get_or_create_current_session(self, session_id: int | None) -> dict:
        """Bootstrap for a client with no (or a possibly-stale) session_id:
        resolves — or creates — the one session currently writable for the
        active project (see ChatSessionManager)."""
        project_name = self._active_project_name
        _, state = self._project_service.get_active_automaton_and_state()
        session = self._session_manager.get_or_create_current_session(
            self._username, project_name, session_id, state.key
        )
        # Always the active one by construction — see
        # ChatSessionManager.get_or_create_current_session.
        return self._session_payload(session, active=True)

    def create_session(self) -> dict:
        """Explicit "new session" action (see session_manager.py's module
        docstring): always starts a fresh session, which immediately
        becomes the active project's writable one. Recorded as starting
        at the automaton's own initial state (init_action.target) —
        not wherever the shared, project-wide automaton position
        currently happens to be — since a brand new session is meant to
        represent starting the conversation over, not picking up whatever
        state other sessions have since moved the project's automaton to
        (that position is a single project-wide fact, unaffected by this;
        see ChatSession.start_state/end_state as just this session's own
        bookkeeping, not the authoritative current state)."""
        project_name = self._active_project_name
        automaton, _ = self._project_service.get_active_automaton_and_state()
        session = self._session_manager.create_session(
            self._username, project_name, automaton.init_action.target
        )
        return self._session_payload(session, active=True)

    def list_sessions(self) -> list[dict]:
        """Every session for the active project, most recently started
        first — for the "Sessions" panel (see ChatWindow.vue). `active`
        on each one (see _session_payload) is what the frontend must
        trust to decide whether that particular session still accepts
        chat turns/manual actions — never computed client-side (see
        ChatSessionManager's module docstring)."""
        project_name = self._active_project_name
        sessions = self._db.list_chat_sessions(self._username, project_name)
        active = self._session_manager.get_active_session(self._username, project_name)
        active_id = active["id"] if active is not None else None
        return [self._session_payload(s, active=(s["id"] == active_id)) for s in sessions]

    def _require_own_session(self, session_id: int) -> None:
        """Raises (404) unless `session_id` still exists and belongs to
        the current user — sessions can now be deleted independently (see
        delete_session), so anything that's about to write to a given
        session_id (open_if_needed, via get_messages) can no longer just
        trust it's still there the way get_or_create_current_session's
        own resolution already does for the write endpoints."""
        session = self._db.get_chat_session(session_id)
        if session is None or session["username"] != self._username:
            raise ChatServiceError("Session not found.", status_code=HTTPStatus.NOT_FOUND)

    def delete_session(self, session_id: int) -> None:
        """Deletes `session_id` and everything scoped to it (see
        db.delete_chat_session) — only the current user's own sessions,
        never someone else's by guessing an id."""
        self._require_own_session(session_id)
        self._db.delete_chat_session(session_id)

    async def get_messages(self, session_id: int, last_n: int | None = None) -> list[dict]:
        # Checked before open_if_needed (which can write an opening
        # message to session_id): a session can be deleted out from under
        # a stale request (e.g. another tab, or a client that hasn't
        # noticed yet) — fail clean instead of an IntegrityError deep in
        # save_message.
        self._require_own_session(session_id)
        # init_action's own message (if any) is deliberately never
        # persisted (see open_if_needed) — the only place it's surfaced.
        init_message = await self.open_if_needed(session_id)
        messages = self._db.get_messages(session_id, last_n=last_n)
        if init_message is not None:
            messages.insert(0, init_message)
        return messages

    async def open_if_needed(self, session_id: int) -> dict | None:
        project_name = self._active_project_name
        automaton, state = self._project_service.get_active_automaton_and_state()

        init_message = None
        if self._db.get_current_state(project_name) is None:
            action = automaton.init_action
            self._db.save_transition(
                "", action.name, state.key, session_id, transition_log_level=state.transition_log_level
            )
            if action.action_prompt:
                init_message = await self._generate_action_prompt_message(
                    action, project_name, session_id, automaton, state
                )

        await self._generate_opening_message_if_needed(project_name, session_id, automaton, state)
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

    def _should_generate_opening_message(self, project_name: str, session_id: int, state: State) -> bool:
        content_since = self._history_cutoff(project_name, state)
        chat_blocked = state.final or not state.chat
        gate_since = self._db.get_last_transition_timestamp(project_name) if chat_blocked else content_since
        return not self._db.has_messages_since(session_id, gate_since)

    async def _generate_opening_message_if_needed(
        self, project_name: str, session_id: int, automaton: Automaton, state: State
    ) -> dict | None:
        if not self._should_generate_opening_message(project_name, session_id, state):
            return None
        return await self._generate_opening_message_body(project_name, session_id, automaton, state)

    async def _generate_opening_message_body(
        self, project_name: str, session_id: int, automaton: Automaton, state: State
    ) -> dict:
        content_since = self._history_cutoff(project_name, state)
        system_prompt, turn_attachments = self._build_turn_prompt(automaton, state)
        chat_history = (
            build_priming_messages(turn_attachments)
            + self._strip_timestamps(self._db.get_messages(session_id, since=content_since))
            + [{"role": "user", "content": "..."}]
        )

        reply = await self._ai_service.generate(system_prompt, chat_history)
        visible_text, tags = _filter_text_and_extract_tags(reply)
        message_id = self._db.save_message("assistant", visible_text, session_id, audio_text=tags['audio'])
        return {"id": message_id, "content": visible_text, "audio_text": tags['audio']}

    async def _generate_action_prompt_message(
        self, action: Action, project_name: str, session_id: int, automaton: Automaton, state: State
    ) -> dict:
        logger.warning("Executing action_prompt for action '%s'.", action.name)

        system_prompt = automaton.general_prompt
        turn_attachments = list(automaton.general_attachments.values()) + list(state.attachments.values())

        since = self._history_cutoff(project_name, state)
        chat_history = (
            build_priming_messages(turn_attachments)
            + self._strip_timestamps(self._db.get_messages(session_id, since=since))
            + [{"role": "user", "content": action.action_prompt}]
        )

        reply = await self._ai_service.generate(system_prompt, chat_history)
        visible_text, tags = _filter_text_and_extract_tags(reply)
        return {"id": None, "content": visible_text, "audio_text": tags['audio']}

    async def _messages_for_transition(
        self, action: Action, project_name: str, session_id: int, automaton: Automaton, new_state: State, *, is_self_loop: bool
    ) -> list[dict]:
        should_open = not is_self_loop and self._should_generate_opening_message(project_name, session_id, new_state)

        messages = []
        if action.action_prompt:
            messages.append(
                await self._generate_action_prompt_message(action, project_name, session_id, automaton, new_state)
            )
        if should_open:
            messages.append(await self._generate_opening_message_body(project_name, session_id, automaton, new_state))
        return messages

    async def _run_auto_tracking(
        self,
        pending_message: dict | None,
        project_name: str,
        session_id: int,
        automaton: Automaton,
        state: State,
        signal_values: dict | None,
    ) -> tuple[Action | None, State, list[dict]]:
        if not self.auto_tracking_enabled:
            return None, state, []

        action, new_state = await self._auto_tracker.run(
            pending_message, project_name, session_id, automaton, state, signal_values
        )
        if action is None:
            return None, state, []

        messages = await self._messages_for_transition(
            action, project_name, session_id, automaton, new_state, is_self_loop=(action.target == state.key)
        )
        return action, new_state, messages

    async def apply_manual_action(self, action_name: str, session_id: int | None) -> dict:
        if self.lock.locked():
            raise ChatServiceError("A chat reply is already being generated.", status_code=HTTPStatus.CONFLICT)
        async with self.lock:
            project_name = self._active_project_name
            _, source_state = self._project_service.get_active_automaton_and_state()
            # Resolved before applying the action: save_transition (inside
            # project_service.apply_manual_action) now needs a session_id.
            session = self._require_active_session(session_id, project_name, source_state.key)
            state_payload, action, source_state_key = self._project_service.apply_manual_action(
                action_name, session["id"]
            )
            automaton, state = self._project_service.get_active_automaton_and_state()
            reply = await self._messages_for_transition(
                action, project_name, session["id"], automaton, state, is_self_loop=(action.target == source_state_key)
            )
            self._session_manager.touch_session(session["id"], state.key)
            return {
                "state": state_payload,
                "reply": reply,
                # A transition can itself call the AI (action_prompt/opening
                # message, via _messages_for_transition above) — piggyback
                # the post-turn model status on this same response so the
                # frontend's model button stays in sync without a separate
                # round trip (see controller.py's GET /api/ai/models).
                "ai_model": self.get_ai_models_info(),
                "session_id": session["id"],
            }

    async def process_turn(
        self,
        text: str,
        session_id: int | None,
        on_retry: OnRetry | None = None,
        on_chunk: OnChunk | None = None,
        on_audio: OnAudio | None = None,
    ) -> dict:
        if self.lock.locked():
            raise ChatServiceError("A chat reply is already being generated.", status_code=HTTPStatus.CONFLICT)
        async with self.lock:
            return await self._process_turn_locked(text, session_id, on_retry, on_chunk, on_audio)

    async def _receive_ai_stream_and_sendreply(self, system_prompt: str, chat_history, filter, on_chunk) -> str:
        reply = ""
        async for chunk in self._ai_service.generate_stream(system_prompt, chat_history):
            chunk = filter.filter(chunk)
            reply += chunk
            if chunk:
                await on_chunk(chunk)
        return reply
    
    async def _process_turn_locked(
        self,
        text: str,
        session_id: int | None,
        on_retry: OnRetry | None,
        on_chunk: OnChunk | None,
        on_audio: OnAudio | None,
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

        session = self._require_active_session(session_id, project_name, state.key)
        resolved_session_id = session["id"]

        if automaton.autotracking_on_user_message:
            action, state, transition_messages = await self._run_auto_tracking(
                pending_message, project_name, resolved_session_id, automaton, state, {}
            )
            messages.extend(transition_messages)

        self._db.save_message("user", text, resolved_session_id)

        if state.chat:
            system_prompt, turn_attachments = self._build_turn_prompt(automaton, state)

            priming_messages = build_priming_messages(turn_attachments)
            since = self._history_cutoff(project_name, state)
            chat_history = priming_messages + self._strip_timestamps(
                self._db.get_messages(resolved_session_id, since=since)
            )

            filter = ConcatTagFilter('audio', 'avance', audio=on_audio)

            if on_chunk is not None:
                reply = await self._receive_ai_stream_and_sendreply(system_prompt, chat_history, filter, on_chunk)
            else:
                reply = await self._ai_service.generate(system_prompt, chat_history, on_retry=on_retry)
                reply = filter.filter_and_flush(reply)

            metadata = _parse_metadata_tag(filter.tags['avance'].tag_content)
            audio_text = filter.tags['audio'].tag_content or None
            assistant_id = self._db.save_message(
                "assistant", reply, resolved_session_id, audio_text=audio_text
            )
            messages.append({"id": assistant_id, "content": reply, "audio_text": audio_text})

            if automaton.autotracking_on_ai_message:
                last_action, state, transition_messages = await self._run_auto_tracking(
                    None, project_name, resolved_session_id, automaton, state, self._metadata_handler.signal_values(metadata)
                )
                if last_action:
                    action = last_action
                messages.extend(transition_messages)

        self._session_manager.touch_session(resolved_session_id, state.key)

        return {
            "reply": messages,
            "state": self._current_state_payload(automaton, state),
            "state_changed": action is not None,
            "new_state": action.target if action else None,
            "triggered_action": action.name if action else None,
            # See apply_manual_action's own "ai_model" for why this rides
            # along with the turn's result instead of a separate call.
            "ai_model": self.get_ai_models_info(),
            "session_id": resolved_session_id,
        }