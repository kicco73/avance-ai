"""SignalService: everything concerning a project's signals lives here —
definitions, auto-tracking (embedded and explicit computation, trigger
evaluation, transition persistence), the read-only /api/chat/signals
payload, and expert annotation (expected_state/expected_values) of a
message's own evaluation point. Architecturally analogous to AiService/
ChatService/MetricService (instantiated once in main.py, constructor-
injected everywhere it's needed) rather than something ChatService
builds for itself — see main.py's own wiring. ChatService depends on
this (message/session ownership checks stay there — see its own
set_message_expected_state/set_message_expected_signals/
clear_session_annotations/get_session_signals, all thin wrappers that
validate ownership first, then delegate here). This also depends
directly on MetricService (trigger-evaluation's own merge_if_referenced,
see AutoTracker.run) and, like ChatService, on AiService — never the
other way around for either: MetricService is a leaf (depends only on
db), so the dependency graph only ever points one way, with nothing
consuming this back."""
from __future__ import annotations

from http import HTTPStatus
from typing import Callable

from automaton.automaton import Action, Automaton, State, SignalPayload
from ai.ai_service import AiService
from chat.env import Env
from chat.metadata_handler import MetadataHandler
from db import Db
from metrics.metric_service import MetricService
from service_error import ServiceError
from signals.auto_tracker import AutoTracker
from signals.definitions import Signals
from signals.evaluator import SignalEvaluator

GetActiveAutomaton = Callable[[], Automaton]
GetUsername = Callable[[], str]
GetActiveProjectName = Callable[[], str]


class SignalServiceError(ServiceError):
    pass


class SignalService(object):
    def __init__(
        self,
        db: Db,
        ai_service: AiService,
        metric_service: MetricService,
        get_active_automaton: GetActiveAutomaton,
        get_username: GetUsername,
        get_active_project_name: GetActiveProjectName,
    ) -> None:
        self._db = db
        self._definitions = Signals(get_active_automaton, db)
        # Own Env instance, independent of whatever ChatService itself
        # tracks — a stateless wrapper around `db` (every call re-reads/
        # writes it fresh, nothing cached), so two separate instances
        # pointed at the same db/user/project stay trivially consistent
        # with each other; nothing here needs to be the literal same
        # object ChatService holds. metric_service, unlike env, is
        # actually shared (constructor-injected from main.py, same
        # instance ChatService itself uses) — see this module's own
        # docstring for why that dependency only ever points one way.
        env = Env(db, get_username, get_active_project_name)
        metadata_handler = MetadataHandler()
        evaluator = SignalEvaluator(metadata_handler)
        self._auto_tracker = AutoTracker(db, ai_service, self._definitions, metric_service, env, evaluator)
        self.auto_tracking_enabled = True

    @property
    def automaton(self) -> Automaton:
        return self._definitions.automaton

    def get_definition(self, names: set[str] | None = None) -> str:
        """The "Definition of signals:" prompt block — see Signals.
        get_definition. `names` (see Automaton.triggerable_signal_names)
        restricts it to a subset; omitted means every declared signal."""
        return self._definitions.get_definition(names)

    def get_latest_signals(self) -> list[SignalPayload]:
        """Read-only, never calls the AI — the GET /api/chat/signals
        payload (see Signals.get_latest_signals)."""
        return self._definitions.get_latest_signals()

    def get_session_signals(self, session_id: int) -> list[dict]:
        """The full Signals event log for `session_id` (see db.
        get_signals) — every snapshot/transition row, chronological.
        Ownership of `session_id` is the caller's own responsibility
        (see ChatService.get_session_signals) — this assumes it's
        already been checked."""
        return self._db.get_signals(session_id)

    async def run_auto_tracking(
        self,
        pending_message: dict | None,
        project_name: str,
        session_id: int,
        automaton: Automaton,
        state: State,
        signal_values: dict | None,
    ) -> tuple[Action | None, State, int | None]:
        """Runs auto-tracking for this turn if enabled — see AutoTracker.
        run for the full contract (embedded vs. explicit computation,
        trigger evaluation, transition persistence). (None, state, None)
        both when auto-tracking is switched off and when AutoTracker.run
        itself finds nothing triggerable to evaluate."""
        if not self.auto_tracking_enabled:
            return None, state, None
        return await self._auto_tracker.run(pending_message, project_name, session_id, automaton, state, signal_values)

    def _require_annotatable_message(self, message_id: int) -> dict:
        """Raises (409) for a message with no linked Signals row at all
        (see Signals.message) — nothing was ever computed for it, so
        there's nothing to annotate against — *unless* it's the one case
        that's still fair game: message_id is its own session's very
        first message and that session never got its own "session
        started here" row (see _materialize_session_start_row). Returns
        the Signals row (not the message) — both annotation setters
        below write straight into it. Assumes `message_id`'s own
        ownership has already been checked by the caller (see
        ChatService.set_message_expected_state/set_message_expected_signals)."""
        row = self._db.get_signal_row_by_message(message_id)
        if row is None:
            row = self._materialize_session_start_row(message_id)
        if row is None:
            raise SignalServiceError(
                "This message isn't an evaluation point — nothing to annotate.",
                status_code=HTTPStatus.CONFLICT,
            )
        return row

    def _materialize_session_start_row(self, message_id: int) -> dict | None:
        """Every session conceptually starts at its own `start_state`, but
        only the literal first session ever opened for a project gets a
        real Signals row for that (see ChatService.open_if_needed's own
        "" -> start_state transition, created once per project, not once
        per session) — every other session's own start has nothing in the
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
            if expected_state == "" or expected_state not in self.automaton.states:
                raise SignalServiceError(
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
            valid_names = {s.name for s in self.automaton.signals}
            for name, value in expected_values.items():
                if name not in valid_names:
                    raise SignalServiceError(
                        f"Unknown signal '{name}'.", status_code=HTTPStatus.UNPROCESSABLE_ENTITY
                    )
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 <= value <= 100):
                    raise SignalServiceError(
                        f"Signal '{name}' must be a number between 0 and 100.",
                        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    )
        self._db.set_signal_expected_values(row["id"], expected_values)
        return self._finalize_annotation_write(row["id"], message_id)

    def clear_session_annotations(self, session_id: int) -> None:
        """Clears every expert annotation (expected_state and
        expected_values alike) across session_id's own Signals rows in
        one call — the "Label sessions" view's "Unlabel all" action.
        Ownership of `session_id` is the caller's own responsibility (see
        ChatService.clear_session_annotations)."""
        self._db.clear_session_annotations(session_id)
