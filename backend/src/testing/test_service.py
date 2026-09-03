"""TestService creates/tracks a Test — a replay of one annotated session,
or every labeled session of a project at once — and the aggregations
built on top of them (per state, per signal, per user, whole-project).
Nothing about a job's own execution is persisted: the durable state is
exclusively Test.results/TestAggregateResult.results, written once a job
completes; TestCache's live registry is the only place "is this still
running" can be answered, and only for the lifetime of this process."""
from __future__ import annotations

from http import HTTPStatus
from typing import cast

from automaton.automaton import Automaton
from ai.ai_service import AiService
from db import Db
from db.tests import _USERNAME_UNSPECIFIED
from jobs import CancelableJob, JobQueue
from project.project_service import ProjectService
from testing.errors import TestServiceError
from testing.cache import TestCache
from testing.last_status_broadcaster import LastStatusBroadcaster
from testing.signal_sources import BatchLiteSignalSource, BatchSignalSource, TurnByTurnSignalSource, estimate_max_turns_per_call
from testing.jobs import (
    AllSignalsAggregationJob,
    AllStatesAggregationJob,
    PooledAggregationJob,
    RootAggregationJob,
    SignalAggregationJob,
    StateAggregationJob,
    TestReplayJob,
    UsersAggregationJob,
)
from tracking.tracking_service import TrackingService

from logging_factory import LoggerFactory
logger = LoggerFactory.get_logger(__name__)

VALID_STRATEGIES = ('batch_lite', 'batch', 'turn_by_turn')

_SIGNAL_SOURCE_CLASS_BY_STRATEGY: dict[str, type] = {
    'batch_lite': BatchLiteSignalSource,
    'batch': BatchSignalSource,
    'turn_by_turn': TurnByTurnSignalSource,
}


class TestService:

    def __init__(
        self, db: Db, ai_service: AiService, tracking_service: TrackingService, job_queue: JobQueue,
        project_service: ProjectService, status_broadcaster: LastStatusBroadcaster,
    ) -> None:
        self._db = db
        self._ai_service = ai_service
        self._tracking_service = tracking_service
        self._job_queue = job_queue
        self._project_service = project_service
        self._status_broadcaster = status_broadcaster
        self._cache = TestCache(db)
        self._jobs_by_key: dict[str, CancelableJob] = {}

    def _submit(self, job: CancelableJob) -> CancelableJob:
        self._jobs_by_key[job.key] = job
        self._job_queue.submit(job)
        return job

    def _track(self, job: CancelableJob) -> CancelableJob:
        # For a job that's one of several dependencies job_queue.submit()
        # will recurse into on its own (never separately submitted here) —
        # still needs a _jobs_by_key entry, otherwise get_jobs_status() has
        # no way to ever see it as anything but idle while it runs.
        self._jobs_by_key[job.key] = job
        return job

    def abort_job(self, key: str) -> None:
        job = self._jobs_by_key.get(key)
        if job is not None:
            self._job_queue.cancel(job)

    def abort_all_jobs(self) -> None:
        for job in list(self._jobs_by_key.values()):
            if not job.is_done() and not job.is_failed() and not job.is_aborted():
                self._job_queue.cancel(job)

    def create_run(self, username: str | None, project_id: str, session_id: int | None, strategy: str) -> dict:
        run, job = self._construct_run(username, project_id, session_id, strategy)
        if job is not None:
            self._submit(job)
        return self._status_for(run)

    def _construct_run(
        self, username: str | None, project_id: str, session_id: int | None, strategy: str,
    ) -> tuple[dict, CancelableJob | None]:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")

        automaton = self._load_automaton(project_id)
        project_draft_edit_count = self._db.get_project_draft_edit_count(project_id)
        session_labeling_revision = self._db.get_session_labeling_revision(session_id) if session_id is not None else None
        ai_model_snapshot = self._ai_service.get_models_info()
        scope_session_ids = self._resolve_scope(username, project_id, session_id)
        signal_source_cls = _SIGNAL_SOURCE_CLASS_BY_STRATEGY[strategy]
        total = (
            self._count_user_messages(scope_session_ids) if strategy == 'turn_by_turn'
            else self._count_batch_segments(scope_session_ids, automaton)
        )

        with self._cache.locked():
            run = self._cache.find(session_id, strategy, project_draft_edit_count, session_labeling_revision)
            job = None
            if run is None:
                run = self._cache.create(
                    username, project_id, session_id, strategy,
                    project_draft_edit_count, session_labeling_revision, ai_model_snapshot,
                )
                job = TestReplayJob(self, run, automaton, scope_session_ids, signal_source_cls, total)
                self._cache.track(run['id'], job)

        return run, job

    def reset_cache(self, project_id: str) -> None:
        run_ids = self._db.delete_tests(project_id)
        self._cache.untrack_many(run_ids)
        self._db.delete_test_aggregate_results(project_id)
        # Otherwise an already-completed job's last broadcast (or its
        # object, for _sessions_job()'s own reuse check) lingers forever —
        # reporting stale 'completed' status for data just deleted above.
        self._jobs_by_key.clear()
        self._status_broadcaster.clear()

    def export_results(self, project_id: str) -> list[dict]:
        return self._db.list_test_aggregate_results(project_id)

    def get_run(self, run_id: int) -> dict:
        run = self._db.get_test(run_id)
        if run is None:
            raise TestServiceError(f"Test {run_id} not found.", status_code=HTTPStatus.NOT_FOUND)
        return self._status_for(run)

    def list_runs(
        self, project_id: str, session_id: int | None = None, username: str | None = _USERNAME_UNSPECIFIED,
    ) -> list[dict]:
        runs = [
            self._status_for(run)
            for run in self._db.list_tests(project_id, session_id, username)
        ]
        return sorted(runs, key=lambda run: run['id'], reverse=True)

    def get_aggregate_result(self, project_id: str, kind: str, target: str | None, strategy: str) -> dict | list[dict] | None:
        edit_count = self._db.get_project_draft_edit_count(project_id)
        return self._db.find_test_aggregate_result(project_id, kind, target, strategy, edit_count)

    def _load_automaton(self, project_id: str) -> Automaton:
        revision = self._db.get_project_revision(project_id)
        return self._project_service.get_automaton(project_id, revision)

    def _project_states(self, project_id: str) -> list[str]:
        automaton = self._load_automaton(project_id)
        return [state.key for state in automaton.states.values() if state.key != ""]

    def _project_signal_names(self, project_id: str) -> list[str]:
        automaton = self._load_automaton(project_id)
        return [signal.name for signal in automaton.signals]

    def _resolve_scope(self, username: str | None, project_id: str, session_id: int | None) -> list[int]:
        if session_id is not None:
            return [session_id]
        # type=None: a whole-project run must cover every labeled session,
        # not just 'live' ones — same reasoning as BenchmarkCalculator._load_sessions.
        sessions = self._db.list_chat_sessions(username, project_id, type=None)
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
        current_draft_edit_count = self._db.get_project_draft_edit_count(run['project_id'])
        stale = current_draft_edit_count != run['project_draft_edit_count']
        job = self._cache.live_job_for(run['id'])
        if run['results'] is not None:
            status, error = 'completed', None
        elif job is not None and cast(CancelableJob, job).is_aborted():
            status, error = 'aborted', None
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

    def start_job(self, project_id: str, state_key: str, strategy: str) -> CancelableJob:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        session_ids = sorted(self._db.get_session_ids_with_expected_state(project_id, state_key))
        job = StateAggregationJob(self, project_id, state_key, strategy, session_ids)
        self._submit(job)
        return job

    def _labeled_session_ids(self, project_id: str) -> list[int]:
        sessions = self._db.list_chat_sessions(None, project_id, type=None)
        return sorted(int(row['id']) for row in sessions if row['labeled'])

    def start_signal_job(self, project_id: str, signal_name: str, strategy: str) -> CancelableJob:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        session_ids = self._labeled_session_ids(project_id)
        job = SignalAggregationJob(self, project_id, signal_name, strategy, session_ids)
        self._submit(job)
        return job

    def start_sessions_run_job(self, project_id: str, strategy: str) -> CancelableJob:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        job = self._sessions_job(project_id, strategy)
        self._submit(job)
        return job

    def _sessions_job(self, project_id: str, strategy: str) -> PooledAggregationJob:
        key = f"{strategy}:sessions-branch"
        session_ids = self._labeled_session_ids(project_id)
        existing = self._jobs_by_key.get(key)
        if (
            existing is not None and not existing.is_aborted() and not existing.is_failed()
            and cast(PooledAggregationJob, existing).session_ids == session_ids
        ):
            return cast(PooledAggregationJob, existing)
        job = PooledAggregationJob(self, project_id, 'sessions', None, strategy, session_ids)
        self._jobs_by_key[key] = job
        return job

    def start_user_sessions_run_job(self, username: str, project_id: str, strategy: str) -> CancelableJob:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        sessions = self._db.list_chat_sessions(username, project_id, type=None)
        session_ids = sorted(int(row['id']) for row in sessions if row['labeled'])
        job = PooledAggregationJob(self, project_id, 'user_sessions', username, strategy, session_ids)
        self._submit(job)
        return job

    def _construct_users_aggregation_job(self, project_id: str, strategy: str) -> CancelableJob:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        sessions = self._db.list_chat_sessions(None, project_id, type=None)
        usernames = sorted({row['username'] for row in sessions if row['labeled']})
        session_ids_by_user = {
            username: sorted(int(row['id']) for row in sessions if row['labeled'] and row['username'] == username)
            for username in usernames
        }
        return UsersAggregationJob(self, project_id, strategy, session_ids_by_user)

    def start_users_aggregation_job(self, project_id: str, strategy: str) -> CancelableJob:
        job = self._construct_users_aggregation_job(project_id, strategy)
        self._submit(job)
        return job

    def _construct_all_states_job(self, project_id: str, strategy: str) -> CancelableJob:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        session_ids_by_state = {
            state_key: sorted(self._db.get_session_ids_with_expected_state(project_id, state_key))
            for state_key in self._project_states(project_id)
        }
        return AllStatesAggregationJob(self, project_id, strategy, session_ids_by_state)

    def start_all_states_job(self, project_id: str, strategy: str) -> CancelableJob:
        job = self._construct_all_states_job(project_id, strategy)
        self._submit(job)
        return job

    def _construct_all_signals_job(self, project_id: str, strategy: str) -> CancelableJob:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        session_ids = self._labeled_session_ids(project_id)
        return AllSignalsAggregationJob(self, project_id, strategy, session_ids, self._project_signal_names(project_id))

    def start_all_signals_job(self, project_id: str, strategy: str) -> CancelableJob:
        job = self._construct_all_signals_job(project_id, strategy)
        self._submit(job)
        return job

    def start_root_job(self, project_id: str, strategy: str) -> CancelableJob:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        branch_jobs = [
            self._sessions_job(project_id, strategy),
            self._construct_all_states_job(project_id, strategy),
            self._construct_users_aggregation_job(project_id, strategy),
            self._construct_all_signals_job(project_id, strategy),
        ]
        job = RootAggregationJob(self, strategy, branch_jobs)
        self._submit(job)
        return job
