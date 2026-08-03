from __future__ import annotations

import asyncio
import logging

from datetime import datetime, timezone
from http import HTTPStatus

from automaton.automaton import Action, Automaton, State
from db import Db, _utc_iso
from ai.ai_service import AiService, OnRetry
from session import Session

from chat.env import Env
from chat.errors import ChatServiceError
from chat.metadata_handler import MetadataHandler
from chat.priming import build_priming_messages
from chat.session_manager import ChatSessionManager
from chat.turn_callbacks import OnChunk, OnMetadata
from chat.turn_processor import TurnProcessor
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from tracking.tracking_service import TrackingService

logger = logging.getLogger(__name__)

FIXED_MESSAGE_INSTRUCTIONS = (
    "You must reply with ONLY a translation of the fixed message below into "
    "the same language the user's last message is written in. Do not answer "
    "or react to what the user said, do not add or remove anything, and do "
    "not change its meaning or formatting — output just the translation.\n\n"
    "Fixed message:\n{fixed_message}"
)


class ChatService(object):
    def __init__(
        self,
        ai_service: AiService,
        project_service: ProjectService,
        db: Db,
        session_manager: ChatSessionManager,
        tracking_service: TrackingService,
        metric_service: MetricService,
    ) -> None:
        self._ai_service = ai_service
        self._project_service = project_service
        self._db = db
        self._session_manager = session_manager
        self.tracking_service = tracking_service
        self.metric_service = metric_service
        self.env = Env(
            db, get_username=lambda: Session().user, get_active_project_name=lambda: project_service.get_active_project_name()
        )
        self._metadata_handler = MetadataHandler()

        # Single-user prototype: serializes chat-turn processing across
        # both transports and against a concurrent reset/activate/upload/
        # delete (main.py's _activate_and_reset awaits this same lock).
        self.lock = asyncio.Lock()

        # The separate "creating a turn" responsibility (see
        # turn_processor.py's own module docstring) — built here, not in
        # main.py, since its own callable dependencies below are this
        # instance's own bound methods (session/message ownership checks,
        # turn-prompt building, the shared transition-message machinery),
        # not something main.py has any business wiring up itself.
        self._turn_processor = TurnProcessor(
            ai_service,
            db,
            tracking_service,
            session_manager,
            self.env,
            # Lambdas, not bound methods directly: some tests construct a
            # ChatService with project_service=None (paths that never
            # reach a real turn) — a bound-method attribute access on
            # None would fail right here, at construction time, instead
            # of only if a turn actually ran (see this class's own `env`
            # just above, which already defers project_service the same
            # way for the same reason).
            get_active_automaton_and_state=lambda: project_service.get_active_automaton_and_state(),
            get_active_project_name=lambda: project_service.get_active_project_name(),
            require_active_session=self._require_active_session,
            build_turn_prompt=self._build_turn_prompt,
            history_cutoff=self._history_cutoff,
            messages_for_transition=self._messages_for_transition,
        )

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
            # Whether any of this session's own Tracking rows carry an
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
        # A brand new session has no messages/Tracking rows yet at all —
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
        always just recomputed fresh from whatever Tracking rows survive).
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
        """The full Tracking event log for `session_id` (see
        TrackingService.get_session_signals) — every snapshot/transition
        row, chronological — for the "Label sessions" view's timeline:
        state transitions and signal values interleaved with messages,
        reconstructed entirely client-side from this one call."""
        self._require_own_session(session_id)
        return self.tracking_service.get_session_signals(session_id)

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

    def _until_from_message(self, message_id: int | None) -> datetime | None:
        """Resolves an optional message_id into a naive-UTC cutoff
        timestamp — the shared "point-in-time Inspector" convention
        behind get_metrics/get_env, keyed by message id rather than a
        raw timestamp so the UI never has to serialize/parse one itself.
        None (live/current) when `message_id` is None."""
        if message_id is None:
            return None
        message = self._require_own_message(message_id)
        # message["timestamp"] is UTC-explicit (see db._utc_iso) for the
        # frontend's own benefit — every DateTimeField column in db.py is
        # naive-but-really-UTC though (default=datetime.utcnow), so the
        # tzinfo must come back off before this is used in a comparison
        # against one, or Python raises on naive-vs-aware comparison.
        return datetime.fromisoformat(message["timestamp"]).replace(tzinfo=None)

    def get_metrics(self, message_id: int | None = None) -> list[dict]:
        """metrics.metrics_framework's core metrics for the active
        user+project — the full current history, or (when `message_id`
        is given) restricted to whatever existed at or before that exact
        message's own timestamp (see MetricService.calculate_all/
        AnalyticsCalculator's `until`) — for the "Label sessions" view's
        point-in-time Inspector."""
        until = self._until_from_message(message_id)
        if until is None:
            return self.metric_service.calculate_all()
        return self.metric_service.calculate_all(until=until)

    def get_env(self, message_id: int | None = None) -> dict:
        """{"stored": ..., "action_set": ..., "computed": ...} — see
        chat.env.Env.stored/action_set/computed, reported separately (not
        merged, unlike Env.to_dict's own use in the turn prompt) so the
        Inspector Env tab knows which section each value belongs in
        ("AI"/"SET"/"COMPUTED") and which are actually editable/deletable
        (see set_env_value/delete_env_key: only the stored — "AI" — ones
        are). Live/current, or (`message_id` given) as of that exact
        message — same point-in-time convention as get_metrics."""
        until = self._until_from_message(message_id)
        return {
            "stored": self.env.stored(until),
            "action_set": self.env.action_set(until),
            "computed": self.env.computed(until),
        }

    def set_env_value(self, key: str, value: str) -> dict:
        """Edits one stored env key (see chat.env.Env.set_value) — always
        live, there's no "editing history". Returns the same shape as
        get_env so the caller can refresh in one round trip. Unlike env
        updates parsed off a reply (always mid-turn, so a session always
        already exists by then), this is a direct human edit that can
        happen before any turn ever ran — db.Db.set_env is otherwise a
        silent no-op without one (see its own docstring), so this
        bootstraps one first, same as a real chat turn would."""
        self.get_or_create_current_session(None)
        self.env.set_value(key, value)
        return self.get_env()

    def delete_env_key(self, key: str) -> dict:
        """Removes one stored env key outright (see chat.env.Env.
        delete_key) — always live. Returns the same shape as get_env."""
        self.get_or_create_current_session(None)
        self.env.delete_key(key)
        return self.get_env()

    def clear_env(self) -> dict:
        """Wipes every stored env key at once (see chat.env.Env.clear) —
        the Inspector Env tab's own "clear all" button for the AI
        section. Always live. Returns the same shape as get_env."""
        self.get_or_create_current_session(None)
        self.env.clear()
        return self.get_env()

    def clear_action_env(self) -> dict:
        """Wipes every action-set env key at once (see chat.env.Env.
        clear_action_set) — the Inspector Env tab's own "clear all"
        button for the ACTION section. Always live. Returns the same
        shape as get_env."""
        self.get_or_create_current_session(None)
        self.env.clear_action_set()
        return self.get_env()

    def get_benchmark_metrics(self, session_id: int | None = None) -> list[dict]:
        """Expert-annotation-vs-actual benchmark metrics (see
        MetricService.get_benchmark_metrics) for the active user+project —
        every annotated session, or (session_id given) just that one —
        the "Label sessions" view's Performance tab. Ownership of
        `session_id`, when given, is checked here; everything else is
        MetricService's own job."""
        if session_id is not None:
            self._require_own_session(session_id)
        return self.metric_service.get_benchmark_metrics(session_id)

    def set_message_expected_state(self, message_id: int, expected_state: str | None) -> dict | None:
        """Sets (expected_state given) or clears (None) the expert-
        annotated expected state for message_id's own evaluation (see
        TrackingService.set_message_expected_state) — ownership of
        `message_id` is checked here, everything else about resolving/
        validating/writing the annotation is TrackingService's own job."""
        self._require_own_message(message_id)
        return self.tracking_service.set_message_expected_state(message_id, expected_state)

    def set_message_expected_signals(self, message_id: int, expected_values: dict | None) -> dict | None:
        """Sets or clears the expert-annotated expected signal values for
        message_id's own evaluation (see TrackingService.
        set_message_expected_signals) — same ownership-then-delegate
        split as set_message_expected_state above."""
        self._require_own_message(message_id)
        return self.tracking_service.set_message_expected_signals(message_id, expected_values)

    def clear_session_annotations(self, session_id: int) -> None:
        """Clears every expert annotation (expected_state and
        expected_values alike) across session_id's own Tracking rows in
        one call (see TrackingService.clear_session_annotations) — the
        "Label sessions" view's "Unlabel all" action, fired only after
        its own confirmation dialog."""
        self._require_own_session(session_id)
        self.tracking_service.clear_session_annotations(session_id)

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
        # "Label sessions" view (see Tracking.message's own docstring),
        # since every other transition ties back to a real user/assistant
        # message but this one otherwise wouldn't.
        opening_message = await self._generate_opening_message_if_needed(project_name, session_id, automaton, state)
        if signal_row_id is not None and opening_message is not None:
            self._db.link_signal_to_message(signal_row_id, opening_message["id"])

        return init_message

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

        # Only the signals a trigger leaving `state` could actually use
        # (see Automaton.triggerable_signal_names) — the embedded
        # signals report this same reply's own [signals] tag carries (see
        # AutoTracker.run's "Embedded" branch) never needs to ask about
        # anything else, since nothing outside this set could affect
        # which action fires from here.
        signal_names = automaton.triggerable_signal_names(state.key)
        signal_definition = self.tracking_service.get_definition(signal_names)
        metadata_prompt = self._metadata_handler.build_prompt(signal_definition, self.env)
        system_prompt = f"{automaton.general_prompt}\n\n{state.contextual_prompt}\n\n{metadata_prompt}"
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
        visible_text, tags = self._metadata_handler._filter_text_and_extract_tags(reply)
        self.env.update(tags['env'])
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
        visible_text, tags = self._metadata_handler._filter_text_and_extract_tags(reply)
        self.env.update(tags['env'])
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

    def _apply_action_env(self, automaton: Automaton, action: Action, signal_values: dict) -> None:
        """A manual action's own version of AutoTracker's own
        _apply_action_env — same expression scope (this call's own
        signal_values, empty for a manual action since no AI computation
        ran; every core metric; env's own current stored+computed
        values), same no-op-when-unset shortcut. Called before
        _messages_for_transition below so a transition's own
        action_prompt/opening message prompt (see _build_turn_prompt)
        already sees the updated env, not last turn's."""
        if not action.env:
            return
        scope = {**signal_values, **self.metric_service.calculate_values(), **self.env.to_dict()}
        updates = automaton.eval_action_env(action, scope)
        if updates:
            self.env.update_action_set(updates)

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
            self._apply_action_env(automaton, action, {})
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
        on_metadata: OnMetadata | None = None,
    ) -> dict:
        if self.lock.locked():
            raise ChatServiceError("A chat reply is already being generated.", status_code=HTTPStatus.CONFLICT)
        async with self.lock:
            return await self._turn_processor.process(text, session_id, on_retry, on_chunk, on_metadata)