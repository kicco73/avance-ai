"""BenchmarkRunService creates/tracks a BenchmarkRun — a replay of one
annotated session, or every labeled session of a project at once — and
the aggregations built on top of them (per state, per signal, per user,
whole-project). Nothing about a job's own execution is persisted: the
durable state is exclusively BenchmarkRun.results/BenchmarkAggregateResult.
results, written once a job completes; BenchmarkRunCache's live registry
is the only place "is this still running" can be answered, and only for
the lifetime of this process."""
from __future__ import annotations

import json
from http import HTTPStatus
from statistics import mean, median, pstdev

import pandas as pd

from automaton.automaton import Automaton
from automaton.automaton_builder import AutomatonBuilder
from ai.ai_service import AiService
from db import Db
from db.benchmark_runs import _USERNAME_UNSPECIFIED
from jobs import Job, JobQueue
from metrics.benchmark_errors import BenchmarkServiceError
from metrics.benchmark_processor import BenchmarkProcessor
from metrics.benchmark_run_cache import BenchmarkRunCache
from metrics.benchmark_run_data import build_benchmark_run_data
from metrics.benchmark_signal_sources import BatchSignalSource, TurnByTurnSignalSource
from metrics.metric_service import BenchmarkMetricsProvider
from metrics.metrics_framework.benchmark_metrics.calculator import BenchmarkCalculator
from metrics.metrics_framework.benchmark_metrics.dto import BenchmarkConfiguration, BenchmarkMetricResult
from metrics.metrics_framework.benchmark_metrics.metrics import SignalAccuracyMetric
from metrics.metrics_framework.benchmark_metrics.observations import BenchmarkData, BenchmarkObservationBuilder
from project.parsers import decode_text_archives
from session import Session
from tracking.env import Env, PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.system_facts import SystemFacts
from tracking.tracking_engine import BenchmarkRunObservationSink, TrackingEngine
from tracking.tracking_service import TrackingService

VALID_STRATEGIES = ('turn_by_turn', 'batch')


def _serialize_metric_result(result: BenchmarkMetricResult) -> dict:
    return {
        'name': result.name,
        'value': result.value,
        'mean': result.mean,
        'median': result.median,
        'standard_deviation': result.standard_deviation,
        'minimum': result.minimum,
        'maximum': result.maximum,
        'sample_count': result.sample_count,
        'components': result.components,
    }


class BenchmarkReplayJob(Job):

    def __init__(
        self, service: "BenchmarkRunService", run: dict, automaton: Automaton,
        session_ids: list[int], signal_source_cls: type, total: int,
    ) -> None:
        key = (
            f"{run['strategy']}:session:{run['session_id']}" if run['session_id'] is not None
            else f"{run['strategy']}:pooled-replay"
        )
        super().__init__(key=key, username=Session().user)
        self._service = service
        self._run = run
        self._automaton = automaton
        self._signal_source_cls = signal_source_cls
        self._total = total
        self._pending_session_ids = list(session_ids)
        self._pending_message_ids: list[int] = []
        self._current_session_id: int | None = None
        self._processor: BenchmarkProcessor | None = None
        self._signal_source = None
        self._warnings: list[str] = []

    def _prepare(self) -> tuple[int, list[Job]]:
        return self._total, []

    @property
    def is_background(self) -> bool:
        return True

    @property
    def result(self) -> str | None:
        return "; ".join(self._warnings) if self._warnings else None

    async def _run_next_step(self) -> None:
        with Session().impersonate(self._run['username']):
            while not self._pending_message_ids:
                if self._current_session_id is not None:
                    self._close_current_session()

                if not self._pending_session_ids:
                    self._finalize()
                    return

                session_id = self._pending_session_ids.pop(0)
                message_ids, warning = self._prepare_session(session_id)
                if warning is not None:
                    self._warnings.append(warning)
                    self._processor = None
                    self._signal_source = None
                    continue
                self._current_session_id = session_id
                self._pending_message_ids = message_ids

            message_id = self._pending_message_ids.pop(0)
            assert self._processor is not None and self._current_session_id is not None
            await self._processor.process_message(self._current_session_id, message_id)

            if not self._pending_message_ids and not self._pending_session_ids:
                self._close_current_session()
                self._finalize()

    def _close_current_session(self) -> None:
        if isinstance(self._signal_source, BatchSignalSource):
            self._service._db.add_benchmark_run_batch_segments(self._run['id'], self._signal_source.batch_segments)
        self._current_session_id = None
        self._processor = None
        self._signal_source = None

    def _finalize(self) -> None:
        self._service._calculate_and_save_results(self._run)
        self._total_steps = self._steps_done + 1

    def _prepare_session(self, session_id: int) -> tuple[list[int], str | None]:
        db = self._service._db
        session = db.get_chat_session(session_id)
        if session is None:
            return [], f"session {session_id}: not found, skipped"
        env = self._service._build_seed_env(session)
        system_facts = SystemFacts()
        session_facts = SessionFacts(db, FixedProjectContext(project_name=self._run['project_name']))
        metrics = BenchmarkMetricsProvider(db, self._run['username'], self._run['project_name'], session_id)
        scope_builder = EvaluationScopeBuilder(env, metrics, system_facts, session_facts)
        sink = BenchmarkRunObservationSink(self._run['id'])
        tracking_engine = TrackingEngine(sink, env, scope_builder)
        self._signal_source = self._signal_source_cls(
            self._service._ai_service, self._service._tracking_service, db, self._automaton, session_id,
        )
        self._processor = BenchmarkProcessor(
            db, self._automaton, tracking_engine, env, session_facts, metrics, self._signal_source, sink,
        )
        return self._processor.prepare(session_id)


_NODE_ID_BY_KIND = {
    'sessions': 'sessions-branch',
    'users': 'users-branch',
    'all_states': 'states-branch',
    'all_signals': 'signals-branch',
}


def _aggregation_node_id(kind: str, target: str | None) -> str:
    if kind in _NODE_ID_BY_KIND:
        return _NODE_ID_BY_KIND[kind]
    if kind == 'user_sessions':
        return f'user:{target}'
    return f'{kind}:{target}'


class _AggregationJob(Job):
    """Common shape for every aggregation job kind: check the cache first,
    compute, persist, return."""

    def __init__(self, service: "BenchmarkRunService", project_name: str, kind: str, target: str | None, strategy: str) -> None:
        super().__init__(key=f"{strategy}:{_aggregation_node_id(kind, target)}", username=Session().user)
        self._service = service
        self._project_name = project_name
        self._kind = kind
        self._target = target
        self._strategy = strategy
        self._result_value: dict | list[dict] | None = None

    def _prepare(self) -> tuple[int, list[Job]]:
        return 1, self._resolve_or_construct_dependencies()

    def _resolve_or_construct_dependencies(self) -> list[Job]:
        raise NotImplementedError

    def _resolve_session_ids(self, session_ids: list[int]) -> tuple[list[int], list[Job]]:
        run_ids = []
        dependencies = []
        for session_id in session_ids:
            run_id, job = self._service._resolve_or_construct_session_run(session_id, self._project_name, self._strategy)
            run_ids.append(run_id)
            if job is not None:
                dependencies.append(job)
        return run_ids, dependencies

    @property
    def is_background(self) -> bool:
        return False

    @property
    def result(self) -> str | None:
        return json.dumps(self._result_value) if self._result_value is not None else None

    def _cached(self) -> dict | list[dict] | None:
        edit_count = self._service._db.get_project_draft_edit_count(self._project_name)
        return self._service._db.find_benchmark_aggregate_result(
            self._project_name, self._kind, self._target, self._strategy, edit_count,
        )

    def _persist(self, result: dict | list[dict]) -> None:
        self._service._persist_aggregate_result(self._project_name, self._kind, self._target, self._strategy, result)

    async def _compute(self) -> dict | list[dict]:
        raise NotImplementedError

    async def _run_next_step(self) -> None:
        cached = self._cached()
        if cached is not None:
            self._result_value = cached
            return
        result = await self._compute()
        self._persist(result)
        self._result_value = result


class StateAggregationJob(_AggregationJob):

    def __init__(self, service: "BenchmarkRunService", project_name: str, state_key: str, strategy: str, session_ids: list[int]) -> None:
        super().__init__(service, project_name, 'state', state_key, strategy)
        self._state_key = state_key
        self._session_ids = session_ids
        self._sub_run_ids: list[int] = []

    def _resolve_or_construct_dependencies(self) -> list[Job]:
        self._sub_run_ids, dependencies = self._resolve_session_ids(self._session_ids)
        return dependencies

    async def _compute(self) -> dict:
        return self._service._aggregate_signal_accuracy(self._sub_run_ids, self._state_key)


class SignalAggregationJob(_AggregationJob):

    def __init__(self, service: "BenchmarkRunService", project_name: str, signal_name: str, strategy: str, session_ids: list[int]) -> None:
        super().__init__(service, project_name, 'signal', signal_name, strategy)
        self._signal_name = signal_name
        self._session_ids = session_ids
        self._sub_run_ids: list[int] = []

    def _resolve_or_construct_dependencies(self) -> list[Job]:
        self._sub_run_ids, dependencies = self._resolve_session_ids(self._session_ids)
        return dependencies

    async def _compute(self) -> dict:
        return self._service._aggregate_signal_name_accuracy(self._sub_run_ids, self._signal_name)


class PooledAggregationJob(_AggregationJob):

    def __init__(
        self, service: "BenchmarkRunService", project_name: str, kind: str, target: str | None, strategy: str,
        session_ids: list[int],
    ) -> None:
        super().__init__(service, project_name, kind, target, strategy)
        self._session_ids = session_ids
        self._sub_run_ids: list[int] = []

    def _resolve_or_construct_dependencies(self) -> list[Job]:
        self._sub_run_ids, dependencies = self._resolve_session_ids(self._session_ids)
        return dependencies

    async def _compute(self) -> list[dict]:
        return self._service._aggregate_pooled_runs(self._sub_run_ids, self._project_name)


class UsersAggregationJob(_AggregationJob):
    """Depends on one PooledAggregationJob('user_sessions', ...) per user
    — not raw session ids directly — so each user's own "user:*" tree
    node gets its own real progress, the same way clicking that user's
    play button standalone (start_user_sessions_run_job) would."""

    def __init__(
        self, service: "BenchmarkRunService", project_name: str, strategy: str, session_ids_by_user: dict[str, list[int]],
    ) -> None:
        super().__init__(service, project_name, 'users', None, strategy)
        self._session_ids_by_user = session_ids_by_user
        self._user_jobs: list[PooledAggregationJob] = []

    def _resolve_or_construct_dependencies(self) -> list[Job]:
        self._user_jobs = [
            PooledAggregationJob(self._service, self._project_name, 'user_sessions', username, self._strategy, session_ids)
            for username, session_ids in self._session_ids_by_user.items()
        ]
        return list(self._user_jobs)

    async def _compute(self) -> list[dict]:
        per_user_results = [json.loads(job.result) for job in self._user_jobs]
        return self._service._aggregate_across_results(per_user_results)


class AllStatesAggregationJob(_AggregationJob):
    """Depends on one StateAggregationJob per state — see
    UsersAggregationJob's own docstring for why."""

    def __init__(
        self, service: "BenchmarkRunService", project_name: str, strategy: str, session_ids_by_state: dict[str, list[int]],
    ) -> None:
        super().__init__(service, project_name, 'all_states', None, strategy)
        self._session_ids_by_state = session_ids_by_state
        self._state_jobs: list[StateAggregationJob] = []

    def _resolve_or_construct_dependencies(self) -> list[Job]:
        self._state_jobs = [
            StateAggregationJob(self._service, self._project_name, state_key, self._strategy, session_ids)
            for state_key, session_ids in self._session_ids_by_state.items()
        ]
        return list(self._state_jobs)

    async def _compute(self) -> dict:
        per_state_results = [json.loads(job.result) for job in self._state_jobs]
        return self._service._aggregate_weighted_by_sample_count(per_state_results)


class AllSignalsAggregationJob(_AggregationJob):

    """Depends on one SignalAggregationJob per signal — see
    UsersAggregationJob's own docstring for why."""

    def __init__(
        self, service: "BenchmarkRunService", project_name: str, strategy: str, session_ids: list[int], signal_names: list[str],
    ) -> None:
        super().__init__(service, project_name, 'all_signals', None, strategy)
        self._session_ids = session_ids
        self._signal_names = signal_names
        self._signal_jobs: list[SignalAggregationJob] = []

    def _resolve_or_construct_dependencies(self) -> list[Job]:
        self._signal_jobs = [
            SignalAggregationJob(self._service, self._project_name, signal_name, self._strategy, self._session_ids)
            for signal_name in self._signal_names
        ]
        return list(self._signal_jobs)

    async def _compute(self) -> dict:
        per_signal_results = [json.loads(job.result) for job in self._signal_jobs]
        return self._service._aggregate_weighted_by_sample_count(per_signal_results)


class RootAggregationJob(Job):

    def __init__(self, service: "BenchmarkRunService", strategy: str, branch_jobs: list[Job]) -> None:
        super().__init__(key=f"{strategy}:root", username=Session().user)
        self._service = service
        self._branch_jobs = branch_jobs

    def _prepare(self) -> tuple[int, list[Job]]:
        return 1, self._branch_jobs

    @property
    def is_background(self) -> bool:
        return False

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        pass


class BenchmarkRunService:

    def __init__(
        self, db: Db, ai_service: AiService, tracking_service: TrackingService, job_queue: JobQueue,
    ) -> None:
        self._db = db
        self._ai_service = ai_service
        self._tracking_service = tracking_service
        self._job_queue = job_queue
        self._cache = BenchmarkRunCache(db)

    def create_run(self, username: str | None, project_name: str, session_id: int | None, strategy: str) -> dict:
        run, job = self._construct_run(username, project_name, session_id, strategy)
        if job is not None:
            self._job_queue.submit(job)
        return self._status_for(run)

    def _construct_run(
        self, username: str | None, project_name: str, session_id: int | None, strategy: str,
    ) -> tuple[dict, Job | None]:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown benchmark run strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")

        automaton = self._load_automaton(project_name)
        project_draft_edit_count = self._db.get_project_draft_edit_count(project_name)
        session_labeling_revision = self._db.get_session_labeling_revision(session_id) if session_id is not None else None
        ai_model_snapshot = self._ai_service.get_models_info()
        scope_session_ids = self._resolve_scope(username, project_name, session_id)
        total = self._count_user_messages(scope_session_ids)
        signal_source_cls = TurnByTurnSignalSource if strategy == 'turn_by_turn' else BatchSignalSource

        with self._cache.locked():
            run = self._cache.find(session_id, strategy, project_draft_edit_count, session_labeling_revision)
            job = None
            if run is None:
                run = self._cache.create(
                    username, project_name, session_id, strategy,
                    project_draft_edit_count, session_labeling_revision, ai_model_snapshot,
                )
                job = BenchmarkReplayJob(self, run, automaton, scope_session_ids, signal_source_cls, total)
                self._cache.track(run['id'], job)

        return run, job

    def reset_cache(self, project_name: str) -> None:
        run_ids = self._db.delete_benchmark_runs(project_name)
        self._cache.untrack_many(run_ids)
        self._db.delete_benchmark_aggregate_results(project_name)

    def export_results(self, project_name: str) -> list[dict]:
        return self._db.list_benchmark_aggregate_results(project_name)

    def get_run(self, run_id: int) -> dict:
        run = self._db.get_benchmark_run(run_id)
        if run is None:
            raise BenchmarkServiceError(f"Benchmark run {run_id} not found.", status_code=HTTPStatus.NOT_FOUND)
        return self._status_for(run)

    def list_runs(
        self, project_name: str, session_id: int | None = None, username: str | None = _USERNAME_UNSPECIFIED,
    ) -> list[dict]:
        runs = [
            self._status_for(run)
            for run in self._db.list_benchmark_runs(project_name, session_id, username)
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
            cached = self._db.find_benchmark_run_by_cache_key(session_id, strategy, edit_count, labeling_revision)
            done = cached is not None and cached['results'] is not None
            session_statuses.append({'session_id': session_id, 'status': 'ok' if done else 'idle'})

        aggregates = []
        for state_key in self._project_states(project_name):
            found = self._db.find_benchmark_aggregate_result(project_name, 'state', state_key, strategy, edit_count)
            aggregates.append({'kind': 'state', 'target': state_key, 'status': 'ok' if found is not None else 'idle'})
        for signal_name in self._project_signal_names(project_name):
            found = self._db.find_benchmark_aggregate_result(project_name, 'signal', signal_name, strategy, edit_count)
            aggregates.append({'kind': 'signal', 'target': signal_name, 'status': 'ok' if found is not None else 'idle'})
        for kind in ('users', 'all_states', 'all_signals'):
            found = self._db.find_benchmark_aggregate_result(project_name, kind, None, strategy, edit_count)
            aggregates.append({'kind': kind, 'target': None, 'status': 'ok' if found is not None else 'idle'})

        return {'sessions': session_statuses, 'aggregates': aggregates}

    def get_aggregate_result(self, project_name: str, kind: str, target: str | None, strategy: str) -> dict | list[dict] | None:
        edit_count = self._db.get_project_draft_edit_count(project_name)
        return self._db.find_benchmark_aggregate_result(project_name, kind, target, strategy, edit_count)

    def _load_automaton(self, project_name: str) -> Automaton:
        archives = self._db.get_archives(project_name)
        if not archives or 'index.yml' not in archives:
            raise ValueError(f"Project '{project_name}' does not exist or has no index.yml.")
        return AutomatonBuilder().build(decode_text_archives(archives))

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

    def _build_seed_env(self, session: dict) -> Env:
        if session['datetime_start'] is None:
            return Env()
        # PersistedEnv now reads Session().user itself — pinned to this
        # historical session's own username for the two calls that need
        # it, then restored (see FixedProjectContext's own docstring).
        with Session().impersonate(session['username']):
            persisted_env = PersistedEnv(self._db, FixedProjectContext(project_name=session['project_name']))
            until = session['datetime_start']
            return Env(stored=persisted_env.stored(until=until), action_set=persisted_env.action_set(until=until))

    def _calculate_and_save_results(self, run: dict) -> None:
        current_run = self._db.get_benchmark_run(run['id'])
        data = build_benchmark_run_data(self._db, current_run)

        if current_run['session_id'] is not None or current_run['username'] is None:
            calculator = BenchmarkCalculator.from_data(data)
        else:
            unfiltered_metrics = BenchmarkCalculator(self._db, current_run['username'], current_run['project_name']).default_metrics()
            calculator = BenchmarkCalculator.from_data(data, metrics=unfiltered_metrics)

        results = [_serialize_metric_result(result) for result in calculator.calculate_all()]
        self._db.set_benchmark_run_results(run['id'], json.dumps(results))

    def _resolve_or_construct_session_run(self, session_id: int, project_name: str, strategy: str) -> tuple[int, Job | None]:
        candidates = [run for run in self.list_runs(project_name, session_id) if run['strategy'] == strategy]
        candidate = candidates[0] if candidates and not candidates[0]['stale'] else None
        if candidate is not None:
            # 'running' — some other branch of the same root click already
            # claimed this exact session — must still be depended on here
            # too, not silently treated as "nothing to wait for" just
            # because a (still in-flight) row already exists.
            if candidate['status'] == 'running':
                live_job = self._cache.live_job_for(candidate['id'])
                assert live_job is not None
                return candidate['id'], live_job
            if candidate['status'] == 'completed':
                return candidate['id'], None
            # 'failed' — a dead attempt; fall through to retry below.
        session = self._db.get_chat_session(session_id)
        assert session is not None
        run, job = self._construct_run(session['username'], project_name, session_id, strategy)
        return run['id'], job

    def _persist_aggregate_result(
        self, project_name: str, kind: str, target: str | None, strategy: str, result: dict | list[dict],
    ) -> None:
        revision = self._db.get_project_revision(project_name)
        edit_count = self._db.get_project_draft_edit_count(project_name)
        self._db.upsert_benchmark_aggregate_result(
            project_name, revision, edit_count, kind, target, strategy, json.dumps(result),
        )


    def start_job(self, project_name: str, state_key: str, strategy: str) -> Job:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown benchmark run strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        session_ids = sorted(self._db.get_session_ids_with_expected_state(project_name, state_key))
        job = StateAggregationJob(self, project_name, state_key, strategy, session_ids)
        self._job_queue.submit(job)
        return job

    def start_signal_job(self, project_name: str, signal_name: str, strategy: str) -> Job:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown benchmark run strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        sessions = self._db.list_chat_sessions(None, project_name, type=None)
        session_ids = sorted(int(row['id']) for row in sessions if row['labeled'])
        job = SignalAggregationJob(self, project_name, signal_name, strategy, session_ids)
        self._job_queue.submit(job)
        return job

    def _construct_sessions_run_job(self, project_name: str, strategy: str) -> Job:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown benchmark run strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        sessions = self._db.list_chat_sessions(None, project_name, type=None)
        session_ids = sorted(int(row['id']) for row in sessions if row['labeled'])
        return PooledAggregationJob(self, project_name, 'sessions', None, strategy, session_ids)

    def start_sessions_run_job(self, project_name: str, strategy: str) -> Job:
        job = self._construct_sessions_run_job(project_name, strategy)
        self._job_queue.submit(job)
        return job

    def start_user_sessions_run_job(self, username: str, project_name: str, strategy: str) -> Job:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown benchmark run strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        sessions = self._db.list_chat_sessions(username, project_name, type=None)
        session_ids = sorted(int(row['id']) for row in sessions if row['labeled'])
        job = PooledAggregationJob(self, project_name, 'user_sessions', username, strategy, session_ids)
        self._job_queue.submit(job)
        return job

    def _construct_users_aggregation_job(self, project_name: str, strategy: str) -> Job:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown benchmark run strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        sessions = self._db.list_chat_sessions(None, project_name, type=None)
        usernames = sorted({row['username'] for row in sessions if row['labeled']})
        session_ids_by_user = {
            username: sorted(int(row['id']) for row in sessions if row['labeled'] and row['username'] == username)
            for username in usernames
        }
        return UsersAggregationJob(self, project_name, strategy, session_ids_by_user)

    def start_users_aggregation_job(self, project_name: str, strategy: str) -> Job:
        job = self._construct_users_aggregation_job(project_name, strategy)
        self._job_queue.submit(job)
        return job

    def _construct_all_states_job(self, project_name: str, strategy: str) -> Job:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown benchmark run strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        session_ids_by_state = {
            state_key: sorted(self._db.get_session_ids_with_expected_state(project_name, state_key))
            for state_key in self._project_states(project_name)
        }
        return AllStatesAggregationJob(self, project_name, strategy, session_ids_by_state)

    def start_all_states_job(self, project_name: str, strategy: str) -> Job:
        job = self._construct_all_states_job(project_name, strategy)
        self._job_queue.submit(job)
        return job

    def _construct_all_signals_job(self, project_name: str, strategy: str) -> Job:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown benchmark run strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        sessions = self._db.list_chat_sessions(None, project_name, type=None)
        session_ids = sorted(int(row['id']) for row in sessions if row['labeled'])
        return AllSignalsAggregationJob(self, project_name, strategy, session_ids, self._project_signal_names(project_name))

    def start_all_signals_job(self, project_name: str, strategy: str) -> Job:
        job = self._construct_all_signals_job(project_name, strategy)
        self._job_queue.submit(job)
        return job

    def start_root_job(self, project_name: str, strategy: str) -> Job:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown benchmark run strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        branch_jobs = [
            self._construct_sessions_run_job(project_name, strategy),
            self._construct_all_states_job(project_name, strategy),
            self._construct_users_aggregation_job(project_name, strategy),
            self._construct_all_signals_job(project_name, strategy),
        ]
        job = RootAggregationJob(self, strategy, branch_jobs)
        self._job_queue.submit(job)
        return job

    def _aggregate_signal_accuracy(self, sub_run_ids: list[int], state_key: str) -> dict:
        observations: list = []
        for run_id in sub_run_ids:
            run = self._db.get_benchmark_run(run_id)
            data = build_benchmark_run_data(self._db, run)
            observations.extend(BenchmarkObservationBuilder(BenchmarkConfiguration()).build(data))

        filtered = tuple(o for o in observations if o.expected_state == state_key)
        return _serialize_metric_result(SignalAccuracyMetric().calculate(filtered))

    def _aggregate_signal_name_accuracy(self, sub_run_ids: list[int], signal_name: str) -> dict:
        observations: list = []
        for run_id in sub_run_ids:
            run = self._db.get_benchmark_run(run_id)
            data = build_benchmark_run_data(self._db, run)
            observations.extend(BenchmarkObservationBuilder(BenchmarkConfiguration()).build(data))

        values = [o.signal_agreements[signal_name] for o in observations if signal_name in o.signal_agreements]
        if not values:
            return {
                'name': signal_name, 'value': 0.0, 'mean': None, 'median': None,
                'standard_deviation': None, 'minimum': None, 'maximum': None,
                'sample_count': 0, 'components': {},
            }
        return {
            'name': signal_name,
            'value': mean(values),
            'mean': mean(values),
            'median': median(values),
            'standard_deviation': pstdev(values) if len(values) > 1 else 0.0,
            'minimum': min(values),
            'maximum': max(values),
            'sample_count': len(values),
            'components': {},
        }

    def _aggregate_pooled_runs(self, sub_run_ids: list[int], project_name: str) -> list[dict]:
        if not sub_run_ids:
            return []
        runs = [self._db.get_benchmark_run(run_id) for run_id in sub_run_ids]
        frames = [build_benchmark_run_data(self._db, run) for run in runs]
        pooled = BenchmarkData(
            messages=pd.concat([f.messages for f in frames], ignore_index=True),
            sessions=pd.concat([f.sessions for f in frames], ignore_index=True),
            signals=pd.concat([f.signals for f in frames], ignore_index=True),
            transitions=pd.concat([f.transitions for f in frames], ignore_index=True),
        )
        unfiltered_metrics = BenchmarkCalculator(self._db, None, project_name).default_metrics()
        calculator = BenchmarkCalculator.from_data(pooled, metrics=unfiltered_metrics)
        return [_serialize_metric_result(result) for result in calculator.calculate_all()]

    def _aggregate_across_results(self, per_group_results: list[list[dict]]) -> list[dict]:
        results_by_name: dict[str, list[dict]] = {}
        for results in per_group_results:
            for result in results:
                results_by_name.setdefault(result['name'], []).append(result)

        aggregated = []
        for name, results in results_by_name.items():
            total_sample_count = sum(result['sample_count'] for result in results)
            with_samples = [result for result in results if result['sample_count']]
            values = [result['value'] for result in with_samples]
            if not values:
                aggregated.append({
                    'name': name, 'value': 0.0, 'mean': None, 'median': None,
                    'standard_deviation': None, 'minimum': None, 'maximum': None,
                    'sample_count': total_sample_count, 'components': {},
                })
                continue
            aggregated.append({
                'name': name,
                'value': mean(values),
                'mean': mean(values),
                'median': median(values),
                'standard_deviation': pstdev(values) if len(values) > 1 else 0.0,
                'minimum': min(values),
                'maximum': max(values),
                'sample_count': total_sample_count,
                'components': with_samples[0]['components'] if len(with_samples) == 1 else {},
            })
        return aggregated

    def _aggregate_weighted_by_sample_count(self, results: list[dict]) -> dict:
        total_sample_count = sum(result['sample_count'] for result in results)
        with_samples = [result for result in results if result['sample_count']]
        if not with_samples:
            return {
                'name': 'overall', 'value': 0.0, 'mean': None, 'median': None,
                'standard_deviation': None, 'minimum': None, 'maximum': None,
                'sample_count': total_sample_count, 'components': {},
            }
        weighted_value = sum(
            result['value'] * result['sample_count'] for result in with_samples
        ) / total_sample_count
        return {
            'name': 'overall',
            'value': weighted_value,
            'mean': None, 'median': None, 'standard_deviation': None, 'minimum': None, 'maximum': None,
            'sample_count': total_sample_count,
            'components': {},
        }
