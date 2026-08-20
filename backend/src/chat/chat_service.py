from __future__ import annotations

import asyncio
import logging

from datetime import datetime, timezone
from http import HTTPStatus

from automaton.automaton import Action, Automaton, State
from db import Db, _utc_iso
from ai.ai_service import AiService
from session import Session

from tracking.automaton_namespace import AutomatonNamespace
from tracking.env import PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.session_facts import SessionFacts
from tracking.system_facts import SystemFacts
from chat.errors import ChatServiceError
from tracking.metadata_handler import MetadataHandler
from chat.session_manager import ChatSessionManager
from chat.session_summary_manager import SessionSummaryManager
from jobs import JobQueue
from tracking.tracking_engine import DbTrackingSink, TrackingEngine
from tracking.turn_callbacks import OnMetadata
from metrics.metric_service import MetricService
from project.project_service import ProjectService
from tracking.tracking_service import TrackingService

logger = logging.getLogger(__name__)

class ChatService(object):
	def __init__(
		self,
		db: Db,
		ai_service: AiService,
		project_service: ProjectService,
		session_manager: ChatSessionManager,
		tracking_service: TrackingService,
		metric_service: MetricService,
		persisted_jobs: JobQueue,
	) -> None:
		self._db = db
		self._ai_service = ai_service
		self._project_service = project_service
		self._session_manager = session_manager
		self.tracking_service = tracking_service
		self.metric_service = metric_service
		# Shares persisted_jobs with BenchmarkRunService (see main.py's own
		# wiring) — never its own private queue.
		self._session_summary_manager = SessionSummaryManager(db, ai_service, persisted_jobs, session_manager)
		get_username = lambda: Session().user
		get_active_project_name = lambda: project_service.get_active_project_name()
		self.env = PersistedEnv(db, get_username=get_username, get_active_project_name=get_active_project_name)
		self._system_facts = SystemFacts()
		self._session_facts = SessionFacts(db, get_username=get_username, get_active_project_name=get_active_project_name)
		self._automaton_namespace = AutomatonNamespace(db, project_service, get_username)
		self.evaluation_scope_builder = EvaluationScopeBuilder(
			self.env, metric_service, self._system_facts, self._session_facts, self._automaton_namespace
		)
		self._metadata_handler = MetadataHandler()
		self._tracking_engine = TrackingEngine(DbTrackingSink(db), self.env, self.evaluation_scope_builder)

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

	def get_session_summary(self, session_id: int) -> dict:
		"""{content: str | None} — None either way whether no
		SessionSummary row exists yet (never queued) or one does but its
		own job hasn't completed yet (see db.get_session_summary/
		SessionSummaryManager). This endpoint only ever answers "is a
		summary ready", never distinguishing those two cases further."""
		summary = self._db.get_session_summary(session_id)
		return {'content': summary['content'] if summary is not None else None}

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
			"source": session["source"],
			"title": session["title"],
			"datetime_start": _utc_iso(session["datetime_start"]),
			"datetime_end": _utc_iso(session["datetime_end"]),
			"start_state": session["start_state"],
			"end_state": session["end_state"],
			# An imported session (see ChatSession.source) has no
			# datetime_end — it never had a live conversation window to
			# begin with, so it's never "open" (ChatSessionManager.is_open
			# itself is null-safe about this for exactly that reason).
			"open": self._session_manager.is_open(session),
			# Distinct from "open" (see session_manager.py's module
			# docstring): the single open session with the most recent
			# datetime_start for this project — what the frontend must
			# trust to decide whether this session still accepts chat
			# turns/manual actions, never computed client-side.
			"active": active,
			# A domain expert's own explicit, persisted verdict (see
			# ChatSession.labeled/ChatService.mark_session_labeled) — the
			# "Label sessions" view's own Sessions panel marker and "Mark
			# done" button state. Read straight off `session` itself now,
			# not recomputed per call the way the old any-Tracking-row-
			# has-an-annotation heuristic needed to be.
			"has_annotations": session["labeled"],
			# A domain expert's own free-text note on the session as a
			# whole (see ChatSession.comment/ChatService.set_session_
			# comment) — the "Label sessions" view's own Info tab.
			"comment": session["comment"],
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
		active project (see ChatSessionManager). Always a real, published-
		revision session — see get_or_create_current_draft_session for
		EditProjectView.vue's own embedded "Test" chat, the only caller
		allowed a draft one instead (see db.create_draft_chat_session's own
		docstring). ValueError (see db.create_chat_session — a project with
		no published revision yet can't have a real chat session) becomes a
		409, same convention as _require_active_session's own.

		Prompt 7: a paused project (see ProjectService.get_project_
		availability) never even attempts to bootstrap a session — the
		frontend's own "Progetto in manutenzione" screen (App.vue) reads
		paused/paused_reason straight off this same response instead of a
		separate round trip, and shows that in place of the chat UI."""
		project_name = self._active_project_name
		is_paused, paused_reason = self._project_service.get_project_availability(project_name)
		if is_paused:
			return {"paused": True, "paused_reason": paused_reason}
		try:
			# No active session means a new one is about to be created —
			# exactly the moment the previously active one (if any) has
			# just been discovered closed (see SessionSummaryManager's own
			# module docstring: there's no other discovery point today).
			if self._session_manager.get_active_session(self._username, project_name) is None:
				self._session_summary_manager.check_for_closed_sessions(self._username, project_name)
			_, state = self._project_service.get_active_automaton_and_state()
			session = self._session_manager.get_or_create_current_session(
				self._username, project_name, session_id, state.key
			)
		except ValueError as exc:
			raise ChatServiceError(str(exc), status_code=HTTPStatus.CONFLICT) from exc
		# Always the active one by construction — see
		# ChatSessionManager.get_or_create_current_session.
		return self._session_payload(session, active=True)

	def get_or_create_current_draft_session(self, session_id: int | None) -> dict:
		"""Like get_or_create_current_session, but the one session currently
		writable for the active project's own *draft* — see db.create_
		draft_chat_session's own docstring for why this exists at all:
		EditProjectView.vue's own embedded "Test" chat (see its own
		ensureDraftChatSession) is the one place a session is allowed to
		exist against a revision nobody's published yet."""
		project_name = self._active_project_name
		try:
			_, state = self._project_service.get_active_draft_automaton_and_state()
			session = self._session_manager.get_or_create_current_draft_session(
				self._username, project_name, session_id, state.key
			)
		except ValueError as exc:
			raise ChatServiceError(str(exc), status_code=HTTPStatus.CONFLICT) from exc
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
		bookkeeping, not the authoritative current state). Always a real,
		published-revision session — see create_draft_session for
		EditProjectView.vue's own embedded "Test" chat, the only caller
		allowed a draft one instead. ValueError (see db.create_chat_session
		— a project with no published revision yet can't have a real chat
		session) becomes a 409, same convention as _require_active_
		session's own."""
		project_name = self._active_project_name
		try:
			automaton, _ = self._project_service.get_active_automaton_and_state()
			session = self._session_manager.create_session(
				self._username, project_name, automaton.init_action.target
			)
		except ValueError as exc:
			raise ChatServiceError(str(exc), status_code=HTTPStatus.CONFLICT) from exc
		# A brand new session has never been marked reviewed — correct by
		# construction (see ChatSession.labeled's own default), no query
		# needed. "on-enter": a brand new session always starts by
		# entering init_action.target *through* init_action itself (see
		# this method's own docstring) — same "on-enter" wire key every
		# other real transition reports (see apply_manual_action/
		# process_turn), just for this one instead of a regular Action.
		return {**self._session_payload(session, active=True), "on-enter": automaton.init_action.on_enter}

	def create_draft_session(self) -> dict:
		"""Like create_session, but always against the active project's own
		current *draft* revision instead (see db.create_draft_chat_session's
		own docstring) — EditProjectView.vue's own embedded "Test" chat is
		the only caller."""
		project_name = self._active_project_name
		try:
			automaton, _ = self._project_service.get_active_draft_automaton_and_state()
			session = self._session_manager.create_draft_session(
				self._username, project_name, automaton.init_action.target
			)
		except ValueError as exc:
			raise ChatServiceError(str(exc), status_code=HTTPStatus.CONFLICT) from exc
		return {**self._session_payload(session, active=True), "on-enter": automaton.init_action.on_enter}

	def _list_sessions_by_source(self, project_name: str, source: str | tuple[str, ...], active_source: str) -> list[dict]:
		sessions = self._db.list_chat_sessions(self._username, project_name, source=source)
		active = self._session_manager.get_active_session(self._username, project_name, source=active_source)
		active_id = active["id"] if active is not None else None
		return [self._session_payload(s, active=(s["id"] == active_id)) for s in sessions]

	def list_sessions(self, include_imported: bool = False) -> list[dict]:
		"""Every real (native, and optionally imported) session for the
		active project, most recently started first — for the "Sessions"
		panel (see ChatWindow.vue). `active` on each one (see
		_session_payload) is what the frontend must trust to decide
		whether that particular session still accepts chat turns/manual
		actions — never computed client-side (see ChatSessionManager's
		module docstring). `include_imported` widens this to native and
		imported alike — used by BenchmarkProjectView's own sessions
		panel, the one place that annotates/tests imported transcripts
		alongside live ones; every other caller keeps seeing native
		sessions only. Never a 'test' session either way (see
		list_test_sessions instead, EditProjectView.vue's own embedded
		"Test" chat) — those are a completely separate pool, not just a
		third source this could also opt into."""
		project_name = self._active_project_name
		source = ('native', 'imported') if include_imported else 'native'
		return self._list_sessions_by_source(project_name, source, active_source='native')

	def list_test_sessions(self) -> list[dict]:
		"""Like list_sessions, but the active project's own 'test' sessions
		(see db.create_draft_chat_session) — EditProjectView.vue's own
		embedded "Test" chat's own Sessions panel, the only caller. Native/
		imported sessions never appear here, symmetric to how a test
		session never appears in list_sessions — the two pools never mix
		in either direction."""
		project_name = self._active_project_name
		return self._list_sessions_by_source(project_name, 'test', active_source='test')

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

	def _reloaded_session_payload(self, session_id: int) -> dict:
		"""Common tail for every "write one field of session_id, then hand
		the frontend back a fresh payload so it can update its own
		Sessions panel without a second round trip" mutation below (title/
		comment/labeled) — re-reads the row post-write and resolves
		`active` the same way for all of them.

		An imported session (see ChatSession.source) is never "active" —
		same convention _list_sessions_by_source's own active_source
		always follows ('native', never 'imported'): "active" means "the
		one session a user may currently write to", which an imported
		transcript never was and never can be. Resolving it as if it
		could be would also crash — an imported session's own
		datetime_end is always None (see ChatSessionManager.is_open),
		not a real window to compare "still open" against at all."""
		session = self._db.get_chat_session(session_id)
		assert session is not None
		if session["source"] == "imported":
			active = False
		else:
			resolved = self._session_manager.get_active_session(self._username, session["project_name"], source=session["source"])
			active = resolved is not None and resolved["id"] == session_id
		return self._session_payload(session, active=active)

	def set_session_title(self, session_id: int, title: str | None) -> dict:
		"""The "Label sessions" view's own Info tab — a domain expert's
		rename for a session, editable for any session regardless of
		source (see ChatSession.title's own docstring: an imported session
		already starts out with one, seeded from its own uploaded
		filename, but native sessions get one this way for the first
		time). Blank/whitespace-only collapses to None, same convention
		set_message_comment already uses for its own optional text field."""
		self._require_own_session(session_id)
		stripped = title.strip() if title is not None else None
		self._db.set_session_title(session_id, stripped or None)
		return self._reloaded_session_payload(session_id)

	def set_session_comment(self, session_id: int, comment: str | None) -> dict:
		"""The "Label sessions" view's own Info tab — a domain expert's
		free-text note on the session as a whole (see ChatSession.comment's
		own docstring), distinct from a per-message comment (Tracking.
		comment/set_message_comment). Same blank-collapses-to-None
		convention as set_session_title."""
		self._require_own_session(session_id)
		stripped = comment.strip() if comment is not None else None
		self._db.set_session_comment(session_id, stripped or None)
		return self._reloaded_session_payload(session_id)

	def mark_session_labeled(self, session_id: int, labeled: bool) -> dict:
		"""The "Label sessions" view's own "Mark done" button (see
		ChatSession.labeled's own docstring) — a domain expert's explicit
		confirmation that this session's been reviewed. A toggle, not a
		one-way action: `labeled=False` un-marks it again, same button,
		same endpoint."""
		self._require_own_session(session_id)
		self._db.set_session_labeled(session_id, labeled)
		return self._reloaded_session_payload(session_id)

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
		_, state = self._project_service.get_automaton_and_state_for_session(session_id)
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
		"""{"stored": ..., "action_set": ...} — see tracking.env.
		PersistedEnv.stored/action_set, reported separately (not merged,
		unlike Env.serialise_as_text's own use in the turn prompt) so the
		Inspector Env tab knows which section each value belongs in
		("AI"/"SET") and which are actually editable/deletable (see
		set_env_value/delete_env_key: only the stored — "AI" — ones are).
		Live/current, or (`message_id` given) as of that exact message —
		same point-in-time convention as get_metrics. No "computed" key
		anymore (system/session facts, see tracking.env's own docstring)
		— those are evaluation-scope-only now, never rendered in the
		Inspector."""
		until = self._until_from_message(message_id)
		return {
			"stored": self.env.stored(until),
			"action_set": self.env.action_set(until),
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

	def set_message_comment(self, message_id: int, comment: str | None) -> dict | None:
		"""Sets or clears message_id's own expert-left free-text comment
		(see TrackingService.set_message_comment) — same ownership-then-
		delegate split as set_message_expected_state above. Unlike that
		one, every message is a legitimate target (no evaluation-point
		gate — see TrackingService._require_commentable_message), so
		there's no ChatServiceError 409 to worry about here, only the
		usual 404 for an unowned/unknown message_id."""
		self._require_own_message(message_id)
		return self.tracking_service.set_message_comment(message_id, comment)

	def clear_session_annotations(self, session_id: int) -> None:
		"""Clears every expert annotation (expected_state and
		expected_values alike) across session_id's own Tracking rows in
		one call (see TrackingService.clear_session_annotations) — the
		"Label sessions" view's "Unlabel all" action, fired only after
		its own confirmation dialog."""
		self._require_own_session(session_id)
		self.tracking_service.clear_session_annotations(session_id)

	async def open_if_needed(self, session_id: int) -> dict | None:
		# An imported session (see ChatSession.source) is a fixed
		# historical transcript, never a live conversation — nothing below
		# ever applies to it (no init transition of its own to bootstrap,
		# no opening message to generate). Without this early return, a
		# project whose *live* conversation currently sits in a final/
		# no-chat state falls into _should_generate_opening_message's own
		# chat_blocked branch, which gates on message timestamps this
		# session's own messages never have (see tracking.session_import's
		# own save_message(..., timestamp=None)) — has_messages_since's
		# `Message.timestamp > since` silently excludes every NULL-
		# timestamp row, so it wrongly reports "no messages since
		# gate_since" and tries to generate one for a session that can
		# never accept it, crashing with "Session is not active" (see
		# _require_active_session, reached via process_turn).
		session = self._db.get_chat_session(session_id)
		if session is not None and session["source"] == "imported":
			return None

		project_name = self._active_project_name
		automaton, state = self._project_service.get_automaton_and_state_for_session(session_id)

		init_message = None
		if self._db.get_current_state(project_name) is None:
			action = automaton.init_action
			# Deliberately never linked to a message here: this transition
			# fires before any message of this bootstrap exists (action_prompt's
			# own, or the plain opening one, generated below), so there's no
			# real "causing" message to attach it to — see
			# TrackingService._materialize_session_start_row, which lazily
			# links it (retroactively, to whatever message an expert first
			# tries to annotate) the same way it already does for every
			# later session's own start.
			self._db.save_transition(
				"", action.name, state.key, session_id, transition_log_level=state.transition_log_level
			)
			if action.action_prompt:
				init_message = await self._generate_action_prompt_message(action, session_id)

		await self._generate_opening_message_if_needed(project_name, session_id, automaton, state)

		return init_message

	def _history_cutoff(self, project_name: str, state: State) -> datetime | None:
		"""Messages at or before this timestamp must be excluded from both
		the AI reply and auto-tracking's signal evaluation, per `state`'s
		history_cutoff. None means "no cutoff, use the full history"."""
		if not state.history_cutoff:
			return None
		return self._db.get_last_transition_timestamp(project_name)

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

		return await self._generate_opening_message_body(session_id)

	async def _generate_opening_message_body(self, session_id: int) -> dict:
		return await self.process_turn(session_id)

	async def _generate_action_prompt_message(self, action: Action, session_id: int) -> dict:
		logger.warning("Executing action_prompt for action '%s'.", action.name)
		return await self.process_turn(session_id, None, extra_prompt=action.action_prompt)

	async def _messages_for_transition(
		self, action: Action, project_name: str, session_id: int, new_state: State, *, is_self_loop: bool
	) -> list[dict]:
		"""Every real, chat-visible message this transition's own follow-up
		turn(s) produced — the action_prompt's own reply, and/or the
		destination state's opening message, in that order — as flat
		{id, role, content, audio_text, timestamp, session_id} rows (see
		db.get_message), the same shape ChatWindow.vue's MessageBubble
		already expects everywhere else. `process_turn`'s own return dict
		(what `_generate_action_prompt_message`/process_turn actually hand
		back) carries only ids, never the message body — a turn whose
		reply landed in a non-chat state has no assistant_message_id at
		all (see TrackingProcessor.process's own early return), which is
		skipped here rather than looked up as None."""
		should_open = not is_self_loop and self._should_generate_opening_message(project_name, session_id, new_state)

		turn_results = []
		if action.action_prompt:
			turn_results.append(await self._generate_action_prompt_message(action, session_id))
		if should_open:
			turn_results.append(await self.process_turn(session_id))

		messages = []
		for turn_result in turn_results:
			message_id = turn_result["assistant_message_id"]
			if message_id is None:
				continue
			message = self._db.get_message(message_id)
			if message is not None:
				messages.append(message)
		return messages

	async def apply_manual_action(self, action_name: str, session_id: int | None) -> dict:
		if self.lock.locked():
			raise ChatServiceError("A chat reply is already being generated.", status_code=HTTPStatus.CONFLICT)
		async with self.lock:
			project_name = self._active_project_name
			# Same "no session specified" ValueError require_active_session
			# itself would raise below — checked explicitly here instead of
			# just letting it happen naturally, since get_automaton_and_state_
			# for_session (unlike get_active_automaton_and_state before it)
			# needs a real session_id to resolve against, not an optional one.
			if session_id is None:
				raise ChatServiceError("No session specified.", status_code=HTTPStatus.CONFLICT)
			_, source_state = self._project_service.get_automaton_and_state_for_session(session_id)
			# Resolved before applying the action: save_transition (inside
			# project_service.apply_manual_action) now needs a session_id.
			session = self._require_active_session(session_id, project_name, source_state.key)
			state_payload, action, source_state_key = self._project_service.apply_manual_action(
				action_name, session["id"]
			)
			automaton, state = self._project_service.get_automaton_and_state_for_session(session["id"])
			self._tracking_engine.apply_action_env(
				automaton, action, {}, source_state_key, username=Session().user, project_name=project_name,
			)
			reply = await self._messages_for_transition(
				action, project_name, session["id"], state, is_self_loop=(action.target == source_state_key)
			)
			self._session_manager.touch_session(session["id"], state.key)
			return {
				"state": state_payload,
				"reply": reply,
				"on-enter": action.on_enter,
				"ai_model": self.get_ai_models_info(),
				"session_id": session["id"],
			}

	async def process_turn(
		self,
		session_id: int,
		text: str | None = None,
		on_metadata: OnMetadata | None = None,
		extra_prompt: str | None = None,
	) -> dict:
		project_name = self._active_project_name
		_, state = self._project_service.get_automaton_and_state_for_session(session_id)
		self._require_active_session(session_id, project_name, state.key)
		reply = await self.tracking_service.process(session_id, text, on_metadata, extra_prompt=extra_prompt)
		# touch_session wants the plain state key (see apply_manual_action's
		# own touch_session(..., state.key) call) — reply['state'] is the
		# full StatePayload dict (see _build_turn_response), not a string;
		# passing it whole silently stored its Python repr as end_state
		# (visible as a rendered-dict title in the Sessions panel).
		self._session_manager.touch_session(reply['session_id'], reply['state']['key'])
		return reply

