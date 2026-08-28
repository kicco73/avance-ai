"""TestService creates/tracks a Test — a replay of one annotated session,
or every labeled session of a project at once — and the aggregations
built on top of them (per state, per signal, per user, whole-project).
Nothing about a job's own execution is persisted: the durable state is
exclusively Test.results/TestAggregateResult.results, written once a job
completes; TestCache's live registry is the only place "is this still
running" can be answered, and only for the lifetime of this process."""
from __future__ import annotations

import json
from http import HTTPStatus
from statistics import mean, median, pstdev

import pandas as pd

from automaton.automaton import Automaton
from automaton.automaton_builder import AutomatonBuilder
from ai.ai_service import AiService
from db import Db
from db.tests import _USERNAME_UNSPECIFIED
from jobs import Job, JobQueue
from testing.errors import TestServiceError
from testing.processor import TestProcessor
from testing.cache import TestCache
from testing.data import build_test_data
from testing.signal_sources import BatchSignalSource, TurnByTurnSignalSource, estimate_max_turns_per_call
from testing.metrics_provider import TestMetricsProvider
from metrics.metrics_framework.benchmark_metrics.calculator import BenchmarkCalculator
from metrics.metrics_framework.benchmark_metrics.dto import BenchmarkConfiguration, BenchmarkMetricResult
from metrics.metrics_framework.benchmark_metrics.metrics import SignalAccuracyMetric, Statistics
from metrics.metrics_framework.benchmark_metrics.observations import BenchmarkData, BenchmarkObservationBuilder
from project.parsers import decode_text_archives
from session import Session
from tracking.env import Env, PersistedEnv
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.system_facts import SystemFacts
from tracking.tracking_engine import TestObservationSink, TrackingEngine
from tracking.tracking_service import TrackingService


from logging_factory import LoggerFactory
logger = LoggerFactory.get_logger(__name__)

VALID_STRATEGIES = ('turn_by_turn', 'batch')



def _job_result(job: Job) -> dict:
    """job.result is only read here once every dependency Job.is_done()
    (guaranteed by the job queue before a dependent's _compute() ever
    runs) — never actually None at this point, just typed that way for
    a job that hasn't finished yet."""
    assert job.result is not None, f"{job.key}: dependency finished without a result"
    return json.loads(job.result)


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
        'distribution': list(result.distribution),
        'components': result.components,
    }


class TestReplayJob(Job):

    def __init__(
        self, service: "TestService", run: dict, automaton: Automaton,
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
        # Each entry is one job "step" = one real AI call: a single
        # message id for turn_by_turn, or a pre-computed group of message
        # ids for batch (see _chunk_into_batches) — decided here, once,
        # upfront, rather than left for BatchSignalSource to discover on
        # its own as it goes.
        self._pending_batches: list[list[int]] = []
        self._current_session_id: int | None = None
        self._processor: TestProcessor | None = None
        self._signal_source = None
        self._warnings: list[str] = []

    def _prepare(self) -> tuple[int, tuple[Job, ...]]:
        # +1: _finalize() always lands on its own dedicated step (see
        # _run_next_step()) — never folded into the last message's step,
        # so the declared total is exact and never needs correcting later.
        return self._total + 1, ()

    @property
    def is_background(self) -> bool:
        return True

    @property
    def result(self) -> str | None:
        return "; ".join(self._warnings) if self._warnings else None

    async def _run_next_step(self) -> None:
        with Session().impersonate(self._run['username']):
            while not self._pending_batches:
                if self._current_session_id is not None:
                    self._close_current_session()

                if not self._pending_session_ids:
                    self._finalize()
                    return

                session_id = self._pending_session_ids.pop(0)
                batches, warning = self._prepare_session(session_id)
                if warning is not None:
                    self._warnings.append(warning)
                    self._processor = None
                    self._signal_source = None
                    continue
                self._current_session_id = session_id
                self._pending_batches = batches

            assert self._processor is not None and self._current_session_id is not None and self._signal_source is not None
            # One step = one real AI call: this batch's group of message
            # ids was decided upfront by _prepare_session (see
            # _chunk_into_batches), so a single BatchSignalSource call
            # covers the whole group before any of its turns are applied.
            batch = self._pending_batches.pop(0)
            if isinstance(self._signal_source, BatchSignalSource):
                await self._signal_source.prepare_batch(batch)
            for message_id in batch:
                await self._processor.process_message(self._current_session_id, message_id)
            # Never finalize here even if this was the last batch — the
            # next call's while loop above finds nothing left and finalizes
            # on its own dedicated step (see _prepare()'s +1).

    def _close_current_session(self) -> None:
        if isinstance(self._signal_source, BatchSignalSource):
            self._service._db.add_test_batch_segments(self._run['id'], self._signal_source.calls_made)
        self._current_session_id = None
        self._processor = None
        self._signal_source = None

    def _finalize(self) -> None:
        self._calculate_and_save_results()
        self._service._cache.untrack_many([self._run['id']])

    def _calculate_and_save_results(self) -> None:
        db = self._service._db
        current_run = db.get_test(self._run['id'])
        assert current_run is not None, f"Test {self._run['id']}: vanished before its own run could finalize"
        data = build_test_data(db, current_run)

        if current_run['session_id'] is not None or current_run['username'] is None:
            calculator = BenchmarkCalculator.from_data(data)
        else:
            unfiltered_metrics = BenchmarkCalculator(db, current_run['username'], current_run['project_name']).default_metrics()
            calculator = BenchmarkCalculator.from_data(data, metrics=unfiltered_metrics)

        results = [_serialize_metric_result(result) for result in calculator.calculate_all()]
        db.set_test_results(self._run['id'], json.dumps(results))

    def _prepare_session(self, session_id: int) -> tuple[list[list[int]], str | None]:
        db = self._service._db
        session = db.get_chat_session(session_id)
        if session is None:
            return [], f"session {session_id}: not found, skipped"
        env = self._build_seed_env(session)
        system_facts = SystemFacts()
        session_facts = SessionFacts(db, FixedProjectContext(project_name=self._run['project_name']))
        metrics = TestMetricsProvider(db, self._run['username'], self._run['project_name'], session_id)
        scope_builder = EvaluationScopeBuilder(env, metrics, system_facts, session_facts)
        sink = TestObservationSink(self._run['id'])
        tracking_engine = TrackingEngine(sink, env, scope_builder)
        self._signal_source = self._signal_source_cls(
            self._service._ai_service, self._service._tracking_service, db, self._automaton, session_id,
        )
        self._processor = TestProcessor(
            db, self._automaton, tracking_engine, env, session_facts, metrics, self._signal_source, sink,
        )
        message_ids, warning = self._processor.prepare(session_id)
        if warning is not None:
            return [], warning
        return self._chunk_into_batches(message_ids), None

    def _build_seed_env(self, session: dict) -> Env:
        if session['datetime_start'] is None:
            return Env()
        # PersistedEnv now reads Session().user itself — pinned to this
        # historical session's own username for the two calls that need
        # it, then restored (see FixedProjectContext's own docstring).
        with Session().impersonate(session['username']):
            persisted_env = PersistedEnv(self._service._db, FixedProjectContext(project_name=session['project_name']))
            until = session['datetime_start']
            return Env(stored=persisted_env.stored(until=until), action_set=persisted_env.action_set(until=until))

    def _chunk_into_batches(self, message_ids: list[int]) -> list[list[int]]:
        if self._signal_source_cls is not BatchSignalSource:
            return [[message_id] for message_id in message_ids]
        max_turns_per_call = estimate_max_turns_per_call(
            len(self._automaton.signals), self._service._ai_service.get_max_output_tokens(),
        )
        return [
            message_ids[i:i + max_turns_per_call]
            for i in range(0, len(message_ids), max_turns_per_call)
        ]


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

    def __init__(self, service: "TestService", project_name: str, kind: str, target: str | None, strategy: str) -> None:
        super().__init__(key=f"{strategy}:{_aggregation_node_id(kind, target)}", username=Session().user)
        self._service = service
        self._project_name = project_name
        self._kind = kind
        self._target = target
        self._strategy = strategy
        self._result_value: dict | list[dict] | None = None

    def _prepare(self) -> tuple[int, tuple[Job, ...]]:
        # Checked here, before any dependency is even resolved — not just
        # in _run_next_step() — so an already-cached node needs no
        # dependencies at all: none of its underlying sessions' (possibly
        # expensive, real-AI-call) TestReplayJobs get constructed or
        # re-run just to re-derive an answer this node already has cached.
        cached = self._cached()
        if cached is not None:
            self._result_value = cached
            return 1, ()
        return 1, self._resolve_or_construct_dependencies()

    def _resolve_or_construct_dependencies(self) -> tuple[Job, ...]:
        raise NotImplementedError

    def _resolve_session_ids(self, session_ids: list[int]) -> tuple[list[int], tuple[Job, ...]]:
        run_ids = []
        dependencies = []
        for session_id in session_ids:
            run_id, job = self._resolve_or_construct_session_run(session_id)
            run_ids.append(run_id)
            if job is not None:
                dependencies.append(job)
        return run_ids, tuple(dependencies)

    def _resolve_or_construct_session_run(self, session_id: int) -> tuple[int, Job | None]:
        candidates = [
            run for run in self._service.list_runs(self._project_name, session_id) if run['strategy'] == self._strategy
        ]
        candidate = candidates[0] if candidates and not candidates[0]['stale'] else None
        if candidate is not None:
            # 'running' — some other branch of the same root click already
            # claimed this exact session — must still be depended on here
            # too, not silently treated as "nothing to wait for" just
            # because a (still in-flight) row already exists.
            if candidate['status'] == 'running':
                live_job = self._service._cache.live_job_for(candidate['id'])
                assert live_job is not None
                return candidate['id'], live_job
            if candidate['status'] == 'completed':
                return candidate['id'], None
            # 'failed' — a dead attempt; fall through to retry below.
        session = self._service._db.get_chat_session(session_id)
        assert session is not None
        run, job = self._service._construct_run(session['username'], self._project_name, session_id, self._strategy)
        return run['id'], job

    def _observations_for_run(self, run_id: int) -> list:
        run = self._service._db.get_test(run_id)
        if run is None:
            return []
        data = build_test_data(self._service._db, run)
        return BenchmarkObservationBuilder(BenchmarkConfiguration()).build(data)

    def _observations_for(self, run_ids: list[int]) -> list:
        observations: list = []
        for run_id in run_ids:
            observations.extend(self._observations_for_run(run_id))
        return observations

    @staticmethod
    def _merge_distributions(results: list[dict]) -> list[int]:
        """Element-wise sum of each result's own histogram — the correct
        way to roll several already-binned distributions (e.g. one per
        sub-group) into the single combined one a branch/root node shows,
        without ever needing the raw per-observation values again."""
        bucket_count = max((len(result.get('distribution') or []) for result in results), default=0)
        if not bucket_count:
            return []
        merged = [0] * bucket_count
        for result in results:
            for i, count in enumerate(result.get('distribution') or []):
                merged[i] += count
        return merged

    def _aggregate_weighted_by_sample_count(self, results: list[dict]) -> dict:
        total_sample_count = sum(result['sample_count'] for result in results)
        with_samples = [result for result in results if result['sample_count']]
        if not with_samples:
            return {
                'name': 'overall', 'value': 0.0, 'mean': None, 'median': None,
                'standard_deviation': None, 'minimum': None, 'maximum': None,
                'sample_count': total_sample_count, 'distribution': self._merge_distributions(results),
                'components': {},
            }
        weighted_value = sum(
            result['value'] * result['sample_count'] for result in with_samples
        ) / total_sample_count
        return {
            'name': 'overall',
            'value': weighted_value,
            'mean': None, 'median': None, 'standard_deviation': None, 'minimum': None, 'maximum': None,
            'sample_count': total_sample_count,
            'distribution': self._merge_distributions(results),
            'components': {},
        }

    @property
    def is_background(self) -> bool:
        return False

    @property
    def result(self) -> str | None:
        return json.dumps(self._result_value) if self._result_value is not None else None

    def _cached(self) -> dict | list[dict] | None:
        edit_count = self._service._db.get_project_draft_edit_count(self._project_name)
        return self._service._db.find_test_aggregate_result(
            self._project_name, self._kind, self._target, self._strategy, edit_count,
        )

    def _persist(self, result: dict | list[dict]) -> None:
        revision = self._service._db.get_project_revision(self._project_name)
        edit_count = self._service._db.get_project_draft_edit_count(self._project_name)
        self._service._db.upsert_test_aggregate_result(
            self._project_name, revision, edit_count, self._kind, self._target, self._strategy, json.dumps(result),
        )

    async def _compute(self) -> dict | list[dict]:
        raise NotImplementedError

    async def _run_next_step(self) -> None:
        # _prepare() already resolved this from cache when possible (see
        # above) — self._result_value is only still None here when it
        # genuinely had to wait on real dependencies.
        if self._result_value is not None:
            return
        result = await self._compute()
        self._persist(result)
        self._result_value = result


class StateAggregationJob(_AggregationJob):

    def __init__(self, service: "TestService", project_name: str, state_key: str, strategy: str, session_ids: list[int]) -> None:
        super().__init__(service, project_name, 'state', state_key, strategy)
        self._state_key = state_key
        self._session_ids = session_ids
        self._sub_run_ids: list[int] = []

    def _resolve_or_construct_dependencies(self) -> tuple[Job, ...]:
        self._sub_run_ids, dependencies = self._resolve_session_ids(self._session_ids)
        return dependencies

    async def _compute(self) -> dict:
        observations = self._observations_for(self._sub_run_ids)
        filtered = tuple(o for o in observations if o.expected_state == self._state_key)
        return _serialize_metric_result(SignalAccuracyMetric().calculate(filtered))


class SignalAggregationJob(_AggregationJob):
    """Unlike its siblings, this one doesn't inherit _AggregationJob's
    single-step _compute() — gathering observations is its slowest part,
    and it scales with session count, so it's spread one run id per step
    (+1 final step for the actual statistics) instead of running as one
    opaque step. Gives real incremental progress and lets a worker yield
    between run ids instead of holding the whole aggregation."""

    def __init__(self, service: "TestService", project_name: str, signal_name: str, strategy: str, session_ids: list[int]) -> None:
        super().__init__(service, project_name, 'signal', signal_name, strategy)
        self._signal_name = signal_name
        self._session_ids = session_ids
        self._sub_run_ids: list[int] = []
        self._pending_run_ids: list[int] = []
        self._observations: list = []

    def _resolve_or_construct_dependencies(self) -> tuple[Job, ...]:
        self._sub_run_ids, dependencies = self._resolve_session_ids(self._session_ids)
        return dependencies

    def _prepare(self) -> tuple[int, tuple[Job, ...]]:
        cached = self._cached()
        if cached is not None:
            self._result_value = cached
            return 1, ()
        dependencies = self._resolve_or_construct_dependencies()
        self._pending_run_ids = list(self._sub_run_ids)
        return len(self._pending_run_ids) + 1, dependencies

    async def _run_next_step(self) -> None:
        if self._result_value is not None:
            return
        if self._pending_run_ids:
            run_id = self._pending_run_ids.pop(0)
            self._observations.extend(self._observations_for_run(run_id))
            return
        result = self._finalize_signal_name_accuracy()
        self._persist(result)
        self._result_value = result

    def _finalize_signal_name_accuracy(self) -> dict:
        values = [
            o.signal_agreements[self._signal_name] for o in self._observations if self._signal_name in o.signal_agreements
        ]
        return _serialize_metric_result(Statistics.result(self._signal_name, values, metadata={"unit": "percent"}))


class PooledAggregationJob(_AggregationJob):

    def __init__(
        self, service: "TestService", project_name: str, kind: str, target: str | None, strategy: str,
        session_ids: list[int],
    ) -> None:
        super().__init__(service, project_name, kind, target, strategy)
        self._session_ids = session_ids
        self._sub_run_ids: list[int] = []

    def _resolve_or_construct_dependencies(self) -> tuple[Job, ...]:
        self._sub_run_ids, dependencies = self._resolve_session_ids(self._session_ids)
        return dependencies

    async def _compute(self) -> list[dict]:
        if not self._sub_run_ids:
            return []
        db = self._service._db
        runs = [run for run_id in self._sub_run_ids if (run := db.get_test(run_id)) is not None]
        frames = [build_test_data(db, run) for run in runs]
        pooled = BenchmarkData(
            messages=pd.concat([f.messages for f in frames], ignore_index=True),
            sessions=pd.concat([f.sessions for f in frames], ignore_index=True),
            signals=pd.concat([f.signals for f in frames], ignore_index=True),
            transitions=pd.concat([f.transitions for f in frames], ignore_index=True),
        )
        unfiltered_metrics = BenchmarkCalculator(db, None, self._project_name).default_metrics()
        calculator = BenchmarkCalculator.from_data(pooled, metrics=unfiltered_metrics)
        return [_serialize_metric_result(result) for result in calculator.calculate_all()]


class UsersAggregationJob(_AggregationJob):
    """Depends on one PooledAggregationJob('user_sessions', ...) per user
    — not raw session ids directly — so each user's own "user:*" tree
    node gets its own real progress, the same way clicking that user's
    play button standalone (start_user_sessions_run_job) would."""

    def __init__(
        self, service: "TestService", project_name: str, strategy: str, session_ids_by_user: dict[str, list[int]],
    ) -> None:
        super().__init__(service, project_name, 'users', None, strategy)
        self._session_ids_by_user = session_ids_by_user
        self._user_jobs: list[PooledAggregationJob] = []

    def _resolve_or_construct_dependencies(self) -> tuple[Job, ...]:
        self._user_jobs = [
            PooledAggregationJob(self._service, self._project_name, 'user_sessions', username, self._strategy, session_ids)
            for username, session_ids in self._session_ids_by_user.items()
        ]
        return tuple(self._user_jobs)

    async def _compute(self) -> list[dict]:
        per_user_results = [_job_result(job) for job in self._user_jobs]
        return self._aggregate_across_results(per_user_results)

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
                    'sample_count': total_sample_count, 'distribution': self._merge_distributions(results),
                    'components': {},
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
                'distribution': self._merge_distributions(results),
                'components': with_samples[0]['components'] if len(with_samples) == 1 else {},
            })
        return aggregated


class AllStatesAggregationJob(_AggregationJob):
    """Depends on one StateAggregationJob per state — see
    UsersAggregationJob's own docstring for why."""

    def __init__(
        self, service: "TestService", project_name: str, strategy: str, session_ids_by_state: dict[str, list[int]],
    ) -> None:
        super().__init__(service, project_name, 'all_states', None, strategy)
        self._session_ids_by_state = session_ids_by_state
        self._state_jobs: list[StateAggregationJob] = []

    def _resolve_or_construct_dependencies(self) -> tuple[Job, ...]:
        self._state_jobs = [
            StateAggregationJob(self._service, self._project_name, state_key, self._strategy, session_ids)
            for state_key, session_ids in self._session_ids_by_state.items()
        ]
        return tuple(self._state_jobs)

    async def _compute(self) -> dict:
        per_state_results = [_job_result(job) for job in self._state_jobs]
        return self._aggregate_weighted_by_sample_count(per_state_results)


class AllSignalsAggregationJob(_AggregationJob):

    """Depends on one SignalAggregationJob per signal — see
    UsersAggregationJob's own docstring for why."""

    def __init__(
        self, service: "TestService", project_name: str, strategy: str, session_ids: list[int], signal_names: list[str],
    ) -> None:
        super().__init__(service, project_name, 'all_signals', None, strategy)
        self._session_ids = session_ids
        self._signal_names = signal_names
        self._signal_jobs: list[SignalAggregationJob] = []

    def _resolve_or_construct_dependencies(self) -> tuple[Job, ...]:
        self._signal_jobs = [
            SignalAggregationJob(self._service, self._project_name, signal_name, self._strategy, self._session_ids)
            for signal_name in self._signal_names
        ]
        return tuple(self._signal_jobs)

    async def _compute(self) -> dict:
        per_signal_results = [_job_result(job) for job in self._signal_jobs]
        return self._aggregate_weighted_by_sample_count(per_signal_results)


class RootAggregationJob(Job):

    def __init__(self, service: "TestService", strategy: str, branch_jobs: list[Job]) -> None:
        super().__init__(key=f"{strategy}:root", username=Session().user)
        self._service = service
        self._branch_jobs = tuple(branch_jobs)

    def _prepare(self) -> tuple[int, tuple[Job, ...]]:
        return 1, self._branch_jobs

    @property
    def is_background(self) -> bool:
        return False

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        pass


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
    ) -> tuple[dict, Job | None]:
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

    def start_job(self, project_name: str, state_key: str, strategy: str) -> Job:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        session_ids = sorted(self._db.get_session_ids_with_expected_state(project_name, state_key))
        job = StateAggregationJob(self, project_name, state_key, strategy, session_ids)
        self._job_queue.submit(job)
        return job

    def start_signal_job(self, project_name: str, signal_name: str, strategy: str) -> Job:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        sessions = self._db.list_chat_sessions(None, project_name, type=None)
        session_ids = sorted(int(row['id']) for row in sessions if row['labeled'])
        job = SignalAggregationJob(self, project_name, signal_name, strategy, session_ids)
        self._job_queue.submit(job)
        return job

    def _construct_sessions_run_job(self, project_name: str, strategy: str) -> Job:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        sessions = self._db.list_chat_sessions(None, project_name, type=None)
        session_ids = sorted(int(row['id']) for row in sessions if row['labeled'])
        return PooledAggregationJob(self, project_name, 'sessions', None, strategy, session_ids)

    def start_sessions_run_job(self, project_name: str, strategy: str) -> Job:
        job = self._construct_sessions_run_job(project_name, strategy)
        self._job_queue.submit(job)
        return job

    def start_user_sessions_run_job(self, username: str, project_name: str, strategy: str) -> Job:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        sessions = self._db.list_chat_sessions(username, project_name, type=None)
        session_ids = sorted(int(row['id']) for row in sessions if row['labeled'])
        job = PooledAggregationJob(self, project_name, 'user_sessions', username, strategy, session_ids)
        self._job_queue.submit(job)
        return job

    def _construct_users_aggregation_job(self, project_name: str, strategy: str) -> Job:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
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
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
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
            raise ValueError(f"Unknown test strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")
        sessions = self._db.list_chat_sessions(None, project_name, type=None)
        session_ids = sorted(int(row['id']) for row in sessions if row['labeled'])
        return AllSignalsAggregationJob(self, project_name, strategy, session_ids, self._project_signal_names(project_name))

    def start_all_signals_job(self, project_name: str, strategy: str) -> Job:
        job = self._construct_all_signals_job(project_name, strategy)
        self._job_queue.submit(job)
        return job

    def start_root_job(self, project_name: str, strategy: str) -> Job:
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

