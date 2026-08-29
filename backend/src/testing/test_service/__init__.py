"""TestService creates/tracks a Test — a replay of one annotated session,
or every labeled session of a project at once — and the aggregations
built on top of them (per state, per signal, per user, whole-project).
Nothing about a job's own execution is persisted: the durable state is
exclusively Test.results/TestAggregateResult.results, written once a job
completes; TestCache's live registry is the only place "is this still
running" can be answered, and only for the lifetime of this process."""
from __future__ import annotations

from http import HTTPStatus

from automaton.automaton import Automaton
from automaton.automaton_builder import AutomatonBuilder
from ai.ai_service import AiService
from db import Db
from db.tests import _USERNAME_UNSPECIFIED
from jobs import CancelableJob, JobQueue
from testing.errors import TestServiceError
from testing.cache import TestCache
from testing.signal_sources import BatchSignalSource, TurnByTurnSignalSource, estimate_max_turns_per_call
from project.archive.layout import ArchiveLayout
from tracking.tracking_service import TrackingService

from .jobs import (
    AllSignalsAggregationJob,
    AllStatesAggregationJob,
    PooledAggregationJob,
    RootAggregationJob,
    SignalAggregationJob,
    StateAggregationJob,
    TestReplayJob,
    UsersAggregationJob,
)

from logging_factory import LoggerFactory
logger = LoggerFactory.get_logger(__name__)

VALID_STRATEGIES = ('turn_by_turn', 'batch')


class TestService:

    def __init__(
        self, db: Db, ai_service: AiService, tracking_service: TrackingService, job_queue: JobQueue,
    ) -> None:
        self._db = db
        self._ai_service = ai_service
        self._tracking_service = tracking_service
        self._job_queue = job_queue
        self._cache = TestCache(db)

    def create_run(self, username: str | None, project_name: str, session_id: int | None, strategy: str) -> dict:
        run, job = self._construct_run(username, project_name, session_id, strategy)
        if job is not None:
            self._job_queue.submit(job)
        return self._status_for(run)

    def _construct_run(
        self, username: str | None, project_name: str, session_id: int | None, strategy: str,
    ) -> tuple[dict, CancelableJob | None]:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")

        automaton = self._load_automaton(project_name)
        project_draft_edit_count = self._db.get_project_draft_edit_count(project_name)
        session_labeling_revision = self._db.get_session_labeling_revision(session_id) if session_id is not None else None
        ai_model_snapshot = self._ai_service.get_models_info()
        scope_session_ids = self._resolve_scope(username, project_name, session_id)
        signal_source_cls = TurnByTurnSignalSource if strategy == 'turn_by_turn' else BatchSignalSource
        total = (
            self._count_user_messages(scope_session_ids) if strategy == 'turn_by_turn'
            else self._count_batch_segments(scope_session_ids, automaton)
        )

        with self._cache.locked():
            run = self._cache.find(session_id, strategy, project_draft_edit_count, session_labeling_revision)
            job = None
            if run is None:
                run = self._cache.create(
                    username, project_name, session_id, strategy,
                    project_draft_edit_count, session_labeling_revision, ai_model_snapshot,
                )
                job = TestReplayJob(self, run, automaton, scope_session_ids, signal_source_cls, total)
                self._cache.track(run['id'], job)

        return run, job

    def reset_cache(self, project_name: str) -> None:
        run_ids = self._db.delete_tests(project_name)
        self._cache.untrack_many(run_ids)
        self._db.delete_test_aggregate_results(project_name)

    def export_results(self, project_name: str) -> list[dict]:
        return self._db.list_test_aggregate_results(project_name)

    def get_run(self, run_id: int) -> dict:
        run = self._db.get_test(run_id)
        if run is None:
            raise TestServiceError(f"Test {run_id} not found.", status_code=HTTPStatus.NOT_FOUND)
        return self._status_for(run)

    def list_runs(
        self, project_name: str, session_id: int | None = None, username: str | None = _USERNAME_UNSPECIFIED,
    ) -> list[dict]:
        runs = [
            self._status_for(run)
            for run in self._db.list_tests(project_name, session_id, username)
        ]
        return sorted(runs, key=lambda run: run['id'], reverse=True)

    def get_jobs_status(self, project_name: str, strategy: str) -> dict:
        edit_count = self._db.get_project_draft_edit_count(project_name)

        sessions = self._db.list_chat_sessions(None, project_name, type=None)
        session_statuses = []
        for row in sessions:
            if not row['labeled']:
                continue
            session_id = int(row['id'])
            labeling_revision = self._db.get_session_labeling_revision(session_id)
            cached = self._db.find_test_by_cache_key(session_id, strategy, edit_count, labeling_revision)
            done = cached is not None and cached['results'] is not None
            session_statuses.append({'session_id': session_id, 'status': 'ok' if done else 'idle'})

        aggregates = []
        for state_key in self._project_states(project_name):
            found = self._db.find_test_aggregate_result(project_name, 'state', state_key, strategy, edit_count)
            aggregates.append({'kind': 'state', 'target': state_key, 'status': 'ok' if found is not None else 'idle'})
        for signal_name in self._project_signal_names(project_name):
            found = self._db.find_test_aggregate_result(project_name, 'signal', signal_name, strategy, edit_count)
            aggregates.append({'kind': 'signal', 'target': signal_name, 'status': 'ok' if found is not None else 'idle'})
        for kind in ('users', 'all_states', 'all_signals'):
            found = self._db.find_test_aggregate_result(project_name, kind, None, strategy, edit_count)
            aggregates.append({'kind': kind, 'target': None, 'status': 'ok' if found is not None else 'idle'})

        return {'sessions': session_statuses, 'aggregates': aggregates}

    def get_aggregate_result(self, project_name: str, kind: str, target: str | None, strategy: str) -> dict | list[dict] | None:
        edit_count = self._db.get_project_draft_edit_count(project_name)
        return self._db.find_test_aggregate_result(project_name, kind, target, strategy, edit_count)

    def _load_automaton(self, project_name: str) -> Automaton:
        archives = self._db.get_archives(project_name)
        if not archives or 'index.yml' not in archives:
            raise ValueError(f"Project '{project_name}' does not exist or has no index.yml.")
        return AutomatonBuilder().build(ArchiveLayout.decode_text(archives))

    def _project_states(self, project_name: str) -> list[str]:
        automaton = self._load_automaton(project_name)
        return [state.key for state in automaton.states.values() if state.key != ""]

    def _project_signal_names(self, project_name: str) -> list[str]:
        automaton = self._load_automaton(project_name)
        return [signal.name for signal in automaton.signals]

    def _resolve_scope(self, username: str | None, project_name: str, session_id: int | None) -> list[int]:
        if session_id is not None:
            return [session_id]
        # type=None: a whole-project run must cover every labeled session,
        # not just 'live' ones — same reasoning as BenchmarkCalculator._load_sessions.
        sessions = self._db.list_chat_sessions(username, project_name, type=None)
        return [int(row['id']) for row in sessions if row['labeled']]

    def _count_user_messages(self, session_ids: list[int]) -> int:
        return sum(
            1 for session_id in session_ids for message in self._db.get_messages(session_id)
            if message['role'] == 'user'
        )

    def _count_batch_segments(self, session_ids: list[int], automaton: Automaton) -> int:
        """Upfront estimate of real AI calls for the batch strategy — one
        step per call, not one per turn (see TestReplayJob._chunk_into_batches,
        which must chunk turns into groups the same way for the final count
        to match this estimate exactly). Uses the project's full signal
        count as the worst case (the widest per-call turn budget could ever
        need once every state has been visited), since which signals a
        given replay actually discovers is only known incrementally, turn
        by turn, not upfront."""
        max_turns_per_call = estimate_max_turns_per_call(
            len(automaton.signals), self._ai_service.get_max_output_tokens()
        )
        total = 0
        for session_id in session_ids:
            turn_count = sum(1 for m in self._db.get_messages(session_id) if m['role'] == 'user')
            total += -(-turn_count // max_turns_per_call) if turn_count else 0
        return total

    def _status_for(self, run: dict) -> dict:
        current_draft_edit_count = self._db.get_project_draft_edit_count(run['project_name'])
        stale = current_draft_edit_count != run['project_draft_edit_count']
        job = self._cache.live_job_for(run['id'])
        if run['results'] is not None:
            status, error = 'completed', None
        elif job is not None:
            status = 'failed' if job.is_failed() else ('completed' if job.is_done() else 'running')
            error = job.error() if status == 'failed' else None
        else:
            status, error = 'failed', 'Job result unavailable (the server may have restarted before it finished).'
        return {
            **run,
            'status': status,
            'error': error,
            'stale': stale,
        }

    def start_job(self, project_name: str, state_key: str, strategy: str) -> CancelableJob:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        session_ids = sorted(self._db.get_session_ids_with_expected_state(project_name, state_key))
        job = StateAggregationJob(self, project_name, state_key, strategy, session_ids)
        self._job_queue.submit(job)
        return job

    def start_signal_job(self, project_name: str, signal_name: str, strategy: str) -> CancelableJob:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        sessions = self._db.list_chat_sessions(None, project_name, type=None)
        session_ids = sorted(int(row['id']) for row in sessions if row['labeled'])
        job = SignalAggregationJob(self, project_name, signal_name, strategy, session_ids)
        self._job_queue.submit(job)
        return job

    def _construct_sessions_run_job(self, project_name: str, strategy: str) -> CancelableJob:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        sessions = self._db.list_chat_sessions(None, project_name, type=None)
        session_ids = sorted(int(row['id']) for row in sessions if row['labeled'])
        return PooledAggregationJob(self, project_name, 'sessions', None, strategy, session_ids)

    def start_sessions_run_job(self, project_name: str, strategy: str) -> CancelableJob:
        job = self._construct_sessions_run_job(project_name, strategy)
        self._job_queue.submit(job)
        return job

    def start_user_sessions_run_job(self, username: str, project_name: str, strategy: str) -> CancelableJob:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        sessions = self._db.list_chat_sessions(username, project_name, type=None)
        session_ids = sorted(int(row['id']) for row in sessions if row['labeled'])
        job = PooledAggregationJob(self, project_name, 'user_sessions', username, strategy, session_ids)
        self._job_queue.submit(job)
        return job

    def _construct_users_aggregation_job(self, project_name: str, strategy: str) -> CancelableJob:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        sessions = self._db.list_chat_sessions(None, project_name, type=None)
        usernames = sorted({row['username'] for row in sessions if row['labeled']})
        session_ids_by_user = {
            username: sorted(int(row['id']) for row in sessions if row['labeled'] and row['username'] == username)
            for username in usernames
        }
        return UsersAggregationJob(self, project_name, strategy, session_ids_by_user)

    def start_users_aggregation_job(self, project_name: str, strategy: str) -> CancelableJob:
        job = self._construct_users_aggregation_job(project_name, strategy)
        self._job_queue.submit(job)
        return job

    def _construct_all_states_job(self, project_name: str, strategy: str) -> CancelableJob:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        session_ids_by_state = {
            state_key: sorted(self._db.get_session_ids_with_expected_state(project_name, state_key))
            for state_key in self._project_states(project_name)
        }
        return AllStatesAggregationJob(self, project_name, strategy, session_ids_by_state)

    def start_all_states_job(self, project_name: str, strategy: str) -> CancelableJob:
        job = self._construct_all_states_job(project_name, strategy)
        self._job_queue.submit(job)
        return job

    def _construct_all_signals_job(self, project_name: str, strategy: str) -> CancelableJob:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        sessions = self._db.list_chat_sessions(None, project_name, type=None)
        session_ids = sorted(int(row['id']) for row in sessions if row['labeled'])
        return AllSignalsAggregationJob(self, project_name, strategy, session_ids, self._project_signal_names(project_name))

    def start_all_signals_job(self, project_name: str, strategy: str) -> CancelableJob:
        job = self._construct_all_signals_job(project_name, strategy)
        self._job_queue.submit(job)
        return job

    def start_root_job(self, project_name: str, strategy: str) -> CancelableJob:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        branch_jobs = [
            self._construct_sessions_run_job(project_name, strategy),
            self._construct_all_states_job(project_name, strategy),
            self._construct_users_aggregation_job(project_name, strategy),
            self._construct_all_signals_job(project_name, strategy),
        ]
        job = RootAggregationJob(self, strategy, branch_jobs)
        self._job_queue.submit(job)
        return job
