from __future__ import annotations

import asyncio
import json
import logging

from datetime import datetime, timezone
from http import HTTPStatus
from typing import Awaitable, Callable, Any

from automaton.automaton import Action, Automaton, State, StatePayload
from db import Db, _utc_iso
from ai.ai_service import AiService, OnRetry
from session import Session

from chat.auto_tracker import AutoTracker
from chat.metadata_handler import MetadataHandler
from chat.metrics_service import ChatMetrics
from chat.priming import build_priming_messages
from chat.session_manager import ChatSessionManager
from chat.signals import Signals
from metrics_framework import BenchmarkCalculator, BenchmarkConfiguration
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

    def _session_payload(self, session: dict, *, active: bool, has_annotations: bool) -> dict:
        return {
            "id": session["id"],
            "project_name": session["project_name"],
            "datetime_start": _utc_iso(session["datetime_start"]),
            "datetime_end": _utc_iso(session["datetime_end"]),
            "start_state": session["start_state"],
            "end_state": session["end_state"],
            "open": self._session_manager.is_open(session),
            # Distinct from "open" (see session_manager.py's module
            # docstring): the single open session with the most recent
            # datetime_start for this project — what the frontend must
            # trust to decide whether this session still accepts chat
            # turns/manual actions, never computed client-side.
            "active": active,
            # Whether any of this session's own Signals rows carry an
            # expert annotation (see db.session_has_annotations) — the
            # "Label sessions" view's own Sessions panel marker.
            "has_annotations": has_annotations,
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
        # ChatSessionManager.get_or_create_current_session. Resolves an
        # existing session as easily as a brand new one, so its own
        # has_annotations is checked for real rather than assumed False.
        return self._session_payload(
            session, active=True, has_annotations=self._db.session_has_annotations(session["id"])
        )

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
        # A brand new session has no messages/Signals rows yet at all —
        # correct by construction, no query needed.
        return self._session_payload(session, active=True, has_annotations=False)

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
        # One query for the whole list, not one per session — see
        # db.get_annotated_session_ids's own docstring.
        annotated_ids = self._db.get_annotated_session_ids(self._username, project_name)
        return [
            self._session_payload(
                s, active=(s["id"] == active_id), has_annotations=s["id"] in annotated_ids
            )
            for s in sessions
        ]

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

    def truncate_session(self, session_id: int, timestamp: str) -> None:
        """"Restart from here" (EditProjectView.vue's own chat only — see
        RestartFromHereButton.vue): deletes every message/signal at or
        after `timestamp` in `session_id` (see db.truncate_session — the
        live automaton state needs no separate rollback of its own, it's
        always just recomputed fresh from whatever Signals rows survive).
        Also refreshes this session's own datetime_end/end_state cache
        (see db.touch_chat_session) to match what's left, rather than
        continuing to report "last active" at a moment nothing survives
        at anymore. `timestamp` is expected to be one of the UTC-explicit
        strings this same backend already handed back (see db._utc_iso —
        the tzinfo comes back off before use, same reasoning as
        get_metrics's own `until`, since every stored column is naive-
        but-really-UTC)."""
        self._require_own_session(session_id)
        cutoff = datetime.fromisoformat(timestamp).replace(tzinfo=None)
        self._db.truncate_session(session_id, cutoff)
        session = self._db.get_chat_session(session_id)
        assert session is not None
        latest = self._db.latest_message_or_signal_timestamp(session_id)
        _, state = self._project_service.get_active_automaton_and_state()
        self._db.touch_chat_session(session_id, latest or session["datetime_start"], state.key)

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

    def get_session_signals(self, session_id: int) -> list[dict]:
        """The full Signals event log for `session_id` (see
        db.get_signals) — every snapshot/transition row, chronological —
        for the "Label sessions" view's timeline: state transitions
        and signal values interleaved with messages, reconstructed
        entirely client-side from this one call."""
        self._require_own_session(session_id)
        return self._db.get_signals(session_id)

    def _require_own_message(self, message_id: int) -> dict:
        """Raises (404) unless `message_id` exists and belongs to a
        session owned by the current user — same ownership contract as
        _require_own_session, just message-scoped (see get_metrics)."""
        message = self._db.get_message(message_id)
        if message is not None:
            session = self._db.get_chat_session(message["session_id"])
            if session is not None and session["username"] == self._username:
                return message
        raise ChatServiceError("Message not found.", status_code=HTTPStatus.NOT_FOUND)

    def get_metrics(self, message_id: int | None = None) -> list[dict]:
        """metrics_framework's core metrics for the active user+project —
        the full current history, or (when `message_id` is given)
        restricted to whatever existed at or before that exact message's
        own timestamp (see ChatMetrics.calculate_all/AnalyticsCalculator's
        `until`) — for the "Label sessions" view's point-in-time
        Inspector, keyed by message id rather than a raw timestamp so the
        UI never has to serialize/parse one itself."""
        if message_id is None:
            return self.metrics.calculate_all()
        message = self._require_own_message(message_id)
        # message["timestamp"] is UTC-explicit (see db._utc_iso) for the
        # frontend's own benefit — every DateTimeField column in db.py is
        # naive-but-really-UTC though (default=datetime.utcnow), so the
        # tzinfo must come back off before this is used in a comparison
        # against one, or Python raises on naive-vs-aware comparison.
        until = datetime.fromisoformat(message["timestamp"]).replace(tzinfo=None)
        return self.metrics.calculate_all(until=until)

    def get_benchmark_metrics(self, session_id: int | None = None) -> list[dict]:
        """Expert-annotation-vs-actual benchmark metrics (see
        metrics_framework/benchmark_metrics) for the active user+project —
        every annotated session, or (session_id given) just that one. Same
        {name, ui_label, ui_description, value} shape as the core metrics
        (see ChatMetrics.calculate_all), plus `sample_count` (how many
        annotated points fed each metric — see the framework's own
        README on why that must never be discarded alongside the score) —
        the "Label sessions" view's Performance tab.
        max_session_duration_in_minutes comes from the same single source
        ChatSessionManager's own open-session window already uses (see
        config.yml's chat-service.max_session_duration_in_minutes) — never
        a second, independently-configured value."""
        if session_id is not None:
            self._require_own_session(session_id)
        configuration = BenchmarkConfiguration(
            max_session_duration_in_minutes=self._session_manager.open_window.total_seconds() / 60.0
        )
        calculator = BenchmarkCalculator(
            self._db, self._username, self._active_project_name, configuration=configuration, session_id=session_id
        )
        results = calculator.calculate_all()
        return [
            {
                "name": metric.name,
                "ui_label": metric.ui_label,
                "ui_description": metric.ui_description,
                "value": result.value,
                "sample_count": result.sample_count,
            }
            for metric, result in zip(calculator.metrics, results)
        ]

    def _require_annotatable_message(self, message_id: int) -> dict:
        """Raises (404) for an unowned/unknown message, (409) for one with
        no linked Signals row at all (see Signals.message) — nothing was
        ever computed for it, so there's nothing to annotate against —
        *unless* it's the one case that's still fair game: message_id is
        its own session's very first message and that session never got
        its own "session started here" row (see
        _materialize_session_start_row). Returns the Signals row (not the
        message) — both annotation setters below write straight into it."""
        self._require_own_message(message_id)
        row = self._db.get_signal_row_by_message(message_id)
        if row is None:
            row = self._materialize_session_start_row(message_id)
        if row is None:
            raise ChatServiceError(
                "This message isn't an evaluation point — nothing to annotate.",
                status_code=HTTPStatus.CONFLICT,
            )
        return row

    def _materialize_session_start_row(self, message_id: int) -> dict | None:
        """Every session conceptually starts at its own `start_state`, but
        only the literal first session ever opened for a project gets a
        real Signals row for that (see open_if_needed's own "" ->
        start_state transition, created once per project, not once per
        session) — every other session's own start has nothing in the
        database to annotate against at all. Rather than leave every
        later session permanently un-annotatable at its own start point
        (see the "Label sessions" view's chat timeline, which shows a
        synthesized row there precisely because there's nothing real to
        show), lazily creates that row here, the first time an expert
        actually tries to annotate it — same shape open_if_needed's own
        eager case uses. Returns None (falls through to the usual 409)
        for anything other than a session's own first message, or a
        session whose start row does exist but is linked elsewhere (must
        never happen in practice, but not this function's job to fix)."""
        message = self._db.get_message(message_id)
        if message is None:
            return None
        session_id = message["session_id"]
        earliest = self._db.get_messages(session_id)
        if not earliest or earliest[0]["id"] != message_id:
            return None
        existing = next(
            (row for row in self._db.get_signals(session_id) if row["old_state"] == ""), None
        )
        if existing is not None:
            if existing["message_id"] is not None:
                return None
            self._db.link_signal_to_message(existing["id"], message_id)
            return self._db.get_signal_row_by_message(message_id)
        session = self._db.get_chat_session(session_id)
        if session is None:
            return None
        self._db.save_transition(
            "", "", session["start_state"], session_id, transition_log_level="INFO", message_id=message_id
        )
        return self._db.get_signal_row_by_message(message_id)

    def _finalize_annotation_write(self, signal_row_id: int, message_id: int) -> dict | None:
        """Re-reads the row just written to — except a session-start
        bookkeeping row (old_state == "", see
        _materialize_session_start_row) left carrying no annotation at
        all afterward, which is deleted instead of kept around as an
        empty husk: it only ever existed to hold that annotation, so
        clearing the last one reverts things to exactly "no row exists
        for this message", same as before it was ever materialized.
        Returns None in that case — the caller (a PUT response) has
        nothing left to describe."""
        updated = self._db.get_signal_row_by_message(message_id)
        assert updated is not None  # just written above, under the same message
        if updated["old_state"] == "" and updated["expected_state"] is None and not updated["expected_values"]:
            self._db.delete_signal_row(signal_row_id)
            return None
        return updated

    def set_message_expected_state(self, message_id: int, expected_state: str | None) -> dict | None:
        """Sets (expected_state given) or clears (None) the expert-
        annotated expected state for message_id's own evaluation — see
        Signals.expected_state's own docstring. Returns the updated
        Signals row, or None if clearing it deleted the row entirely (see
        _finalize_annotation_write). `expected_state` must name a real
        state in the active project's own automaton — the "Benchmark
        project" view's States dropdown is populated from exactly that
        list, but this is the one place that actually enforces it."""
        row = self._require_annotatable_message(message_id)
        if expected_state is not None:
            automaton, _ = self._project_service.get_active_automaton_and_state()
            if expected_state == "" or expected_state not in automaton.states:
                raise ChatServiceError(
                    f"Unknown state '{expected_state}'.", status_code=HTTPStatus.UNPROCESSABLE_ENTITY
                )
        self._db.set_signal_expected_state(row["id"], expected_state)
        return self._finalize_annotation_write(row["id"], message_id)

    def set_message_expected_signals(self, message_id: int, expected_values: dict | None) -> dict | None:
        """Sets or clears the expert-annotated expected signal values for
        message_id's own evaluation — see Signals.expected_values's own
        docstring. `expected_values` is the *whole* replacement dict: a
        signal name missing from it is annotation-cleared for that signal
        alone (the "Label sessions" view's own sliders send the whole
        dict on every change, never a single-key patch). Every key must
        name a real signal in the active project, every value a plain
        number in [0, 100] (see Inspector.vue's own slider range). Returns
        the updated Signals row, or None if clearing it deleted the row
        entirely (see _finalize_annotation_write)."""
        row = self._require_annotatable_message(message_id)
        if expected_values:
            automaton, _ = self._project_service.get_active_automaton_and_state()
            valid_names = {s.name for s in automaton.signals}
            for name, value in expected_values.items():
                if name not in valid_names:
                    raise ChatServiceError(
                        f"Unknown signal '{name}'.", status_code=HTTPStatus.UNPROCESSABLE_ENTITY
                    )
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 <= value <= 100):
                    raise ChatServiceError(
                        f"Signal '{name}' must be a number between 0 and 100.",
                        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    )
        self._db.set_signal_expected_values(row["id"], expected_values)
        return self._finalize_annotation_write(row["id"], message_id)

    def clear_session_annotations(self, session_id: int) -> None:
        """Clears every expert annotation (expected_state and
        expected_values alike) across session_id's own Signals rows in
        one call — the "Label sessions" view's "Unlabel all" action,
        fired only after its own confirmation dialog."""
        self._require_own_session(session_id)
        self._db.clear_session_annotations(session_id)

    async def open_if_needed(self, session_id: int) -> dict | None:
        project_name = self._active_project_name
        automaton, state = self._project_service.get_active_automaton_and_state()

        init_message = None
        # Set only when this call is the one that actually creates the
        # automaton's own "" -> start_state transition (a session's very
        # first) — never on a later call for an already-bootstrapped
        # session, even if that call still generates its own opening
        # message for some other reason (see _should_generate_opening_message).
        signal_row_id = None
        if self._db.get_current_state(project_name) is None:
            action = automaton.init_action
            signal_row_id = self._db.save_transition(
                "", action.name, state.key, session_id, transition_log_level=state.transition_log_level
            )
            if action.action_prompt:
                init_message = await self._generate_action_prompt_message(
                    action, project_name, session_id, automaton, state
                )

        # init_action's action_prompt reply (if any, see above) is
        # deliberately never persisted (see get_messages' own docstring)
        # — there's no message to link the init transition to there. Its
        # own *opening* message (a real, persisted one) is the closest
        # thing to "the message whose processing produced this
        # transition" it actually has — without this, a session's very
        # first transition could never be annotated at all in the
        # "Label sessions" view (see Signals.message's own docstring),
        # since every other transition ties back to a real user/assistant
        # message but this one otherwise wouldn't.
        opening_message = await self._generate_opening_message_if_needed(project_name, session_id, automaton, state)
        if signal_row_id is not None and opening_message is not None:
            self._db.link_signal_to_message(signal_row_id, opening_message["id"])

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
    ) -> tuple[Action | None, State, list[dict], int | None]:
        """The trailing `int | None` is the id of whatever Signals row this
        call's own evaluation persisted (None if auto-tracking is off, or
        this state has nothing triggerable to evaluate at all — see
        AutoTracker.run) — the caller links it to the message that caused
        this call, once that message itself has an id (see
        _process_turn_locked/link_signal_to_message)."""
        if not self.auto_tracking_enabled:
            return None, state, [], None

        action, new_state, signal_row_id = await self._auto_tracker.run(
            pending_message, project_name, session_id, automaton, state, signal_values
        )
        if action is None:
            return None, state, [], signal_row_id

        messages = await self._messages_for_transition(
            action, project_name, session_id, automaton, new_state, is_self_loop=(action.target == state.key)
        )
        return action, new_state, messages, signal_row_id

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
                # on-enter belongs to the action that was actually fired —
                # not to whatever's in state_payload's own outgoing
                # actions list (a different set entirely, the *new*
                # state's own actions) — see automaton.Action.on_enter.
                # Kebab-cased to match the YAML field's own spelling (see
                # automaton_builder.py's _build_action) end to end, unlike
                # every other snake_case key here.
                "on-enter": action.on_enter,
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

        action: Action | None = None
        project_name = self._active_project_name
        messages: list[dict] = []

        session = self._require_active_session(session_id, project_name, state.key)
        resolved_session_id = session["id"]

        # Saved *before* auto-tracking runs — not after (see git history/
        # PR discussion for why this was briefly reverted and why that was
        # wrong): auto-tracking's own evaluation for this message can fire
        # a transition, which can itself generate follow-up messages (an
        # action_prompt, an opening message — including a fixed_message
        # translation, see _build_turn_prompt) via _messages_for_transition
        # below. Every one of those is a *reaction* to this user message —
        # if it were saved first, they'd all get an earlier timestamp than
        # the very message that caused them, corrupting the persisted
        # chronological order (not just how the "Label sessions" view's
        # timeline happens to display it — every consumer of
        # db.get_messages/get_signals' own timestamp ordering, e.g.
        # chat_history for the next AI call, would see it too).
        # Passing pending_message=None to _run_auto_tracking below (rather
        # than this message's own not-yet-persisted content) is safe:
        # _signal_history_window falls back to fetching it straight from
        # the db instead, and since it's already saved by the time that
        # runs, the resulting transcript is identical either way.
        user_message_id = self._db.save_message("user", text, resolved_session_id)

        signal_row_id = None
        if automaton.autotracking_on_user_message:
            action, state, transition_messages, signal_row_id = await self._run_auto_tracking(
                None, project_name, resolved_session_id, automaton, state, {}
            )
            messages.extend(transition_messages)
            if signal_row_id is not None:
                self._db.link_signal_to_message(signal_row_id, user_message_id)

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
                last_action, state, transition_messages, signal_row_id = await self._run_auto_tracking(
                    None, project_name, resolved_session_id, automaton, state, self._metadata_handler.signal_values(metadata)
                )
                if last_action:
                    action = last_action
                messages.extend(transition_messages)
                if signal_row_id is not None:
                    self._db.link_signal_to_message(signal_row_id, assistant_id)

        self._session_manager.touch_session(resolved_session_id, state.key)

        return {
            "reply": messages,
            "state": self._current_state_payload(automaton, state),
            "state_changed": action is not None,
            "new_state": action.target if action else None,
            "triggered_action": action.name if action else None,
            # The fired action's own on_enter (see automaton.Action.
            # on_enter) — None both when no transition happened this turn
            # and when the action that did fire simply has none set.
            # Kebab-cased key, matching the YAML field's own spelling.
            "on-enter": action.on_enter if action else None,
            # See apply_manual_action's own "ai_model" for why this rides
            # along with the turn's result instead of a separate call.
            "ai_model": self.get_ai_models_info(),
            "session_id": resolved_session_id,
        }