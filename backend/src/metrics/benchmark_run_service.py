"""BenchmarkRunService creates/tracks a BenchmarkRun — a replay of one
annotated session, or every labeled session of a project at once. The
run's lifecycle lives on the generic Job engine, linked by reference_id."""
from __future__ import annotations

import asyncio
import json
from http import HTTPStatus

from automaton.automaton import Automaton
from automaton.automaton_builder import AutomatonBuilder
from ai.ai_service import AiService
from db import Db
from db.benchmark_runs import _USERNAME_UNSPECIFIED
from jobs import JobQueue, JobWork, OnProgress
from metrics.benchmark_errors import BenchmarkServiceError
from metrics.benchmark_processor import BenchmarkProcessor
from metrics.benchmark_run_data import build_benchmark_run_data
from metrics.benchmark_signal_sources import BatchSignalSource, TurnByTurnSignalSource
from metrics.metric_service import BenchmarkMetricsProvider
from metrics.metrics_framework.benchmark_metrics.calculator import BenchmarkCalculator
from metrics.metrics_framework.benchmark_metrics.dto import BenchmarkConfiguration, BenchmarkMetricResult
from metrics.metrics_framework.benchmark_metrics.metrics import SignalAccuracyMetric
from metrics.metrics_framework.benchmark_metrics.observations import BenchmarkObservationBuilder
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


class BenchmarkRunService:

    def __init__(
        self, db: Db, ai_service: AiService, tracking_service: TrackingService,
        persisted_jobs: JobQueue, ephemeral_jobs: JobQueue,
    ) -> None:
        self._db = db
        self._ai_service = ai_service
        self._tracking_service = tracking_service
        self._persisted_jobs = persisted_jobs
        self._ephemeral_jobs = ephemeral_jobs

    def create_run(self, username: str | None, project_name: str, session_id: int | None, strategy: str) -> dict:
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown benchmark run strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")

        automaton = self._load_automaton(project_name)
        project_revision = self._db.get_project_revision(project_name)
        ai_model_snapshot = self._ai_service.get_models_info()

        run = self._db.create_benchmark_run(
            username, project_name, session_id, strategy, project_revision, ai_model_snapshot,
        )

        scope_session_ids = self._resolve_scope(username, project_name, session_id)
        total = self._count_user_messages(scope_session_ids)

        signal_source_cls = TurnByTurnSignalSource if strategy == 'turn_by_turn' else BatchSignalSource
        work = self._build_replay_work(run, automaton, scope_session_ids, signal_source_cls)

        self._persisted_jobs.submit(kind='benchmark_run', reference_id=run['id'], total=total, work=work)

        return self._merge_with_job(run)

    def get_run(self, run_id: int) -> dict:
        run = self._db.get_benchmark_run(run_id)
        if run is None:
            raise BenchmarkServiceError(f"Benchmark run {run_id} not found.", status_code=HTTPStatus.NOT_FOUND)
        return self._merge_with_job(run)

    def list_runs(
        self, project_name: str, session_id: int | None = None, username: str | None = _USERNAME_UNSPECIFIED,
    ) -> list[dict]:
        runs = [
            self._merge_with_job(run)
            for run in self._db.list_benchmark_runs(project_name, session_id, username)
        ]
        return sorted(runs, key=lambda run: run['created_at'], reverse=True)

    def _load_automaton(self, project_name: str) -> Automaton:
        archives = self._db.get_archives(project_name)
        if not archives or 'index.yml' not in archives:
            raise ValueError(f"Project '{project_name}' does not exist or has no index.yml.")
        return AutomatonBuilder().build(decode_text_archives(archives))

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

    def _merge_with_job(self, run: dict) -> dict:
        job = self._db.get_job_by_reference('benchmark_run', run['id'])
        # Revision comparison, not a timestamp: Project.revision only bumps
        # on publish, so a run stays "fresh" across further unpublished
        # edits to the same draft — a known, accepted imprecision.
        current_revision = self._db.get_project_revision(run['project_name'])
        return {
            **run,
            'status': job['status'],
            'created_at': job['created_at'],
            'finished_at': job['finished_at'],
            'error': job['error'],
            'processed_messages': job['progress_current'],
            'total_messages': job['progress_total'],
            'stale': current_revision != run['project_revision'],
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

    def _build_replay_work(
        self, run: dict, automaton: Automaton, session_ids: list[int], signal_source_cls: type,
    ) -> JobWork:
        async def work(on_progress: OnProgress) -> tuple[str | None, str | None]:
            warnings: list[str] = []
            completed_turns = 0

            # SessionFacts (like PersistedEnv) now reads Session().user
            # itself rather than taking it as a constructor argument —
            # pinned to this run's own username for the whole replay,
            # then restored, since a job worker may reuse this same
            # context/task across unrelated jobs afterward (see
            # FixedProjectContext's own docstring for why it must never
            # read whatever's live instead).
            with Session().impersonate(run['username']):
                for session_id in session_ids:
                    session = self._db.get_chat_session(session_id)
                    env = self._build_seed_env(session)
                    system_facts = SystemFacts()
                    session_facts = SessionFacts(self._db, FixedProjectContext(project_name=run['project_name']))
                    metrics = BenchmarkMetricsProvider(self._db, run['username'], run['project_name'], session_id)
                    scope_builder = EvaluationScopeBuilder(env, metrics, system_facts, session_facts)
                    sink = BenchmarkRunObservationSink(run['id'])
                    tracking_engine = TrackingEngine(sink, env, scope_builder)
                    signal_source = signal_source_cls(self._ai_service, self._tracking_service, self._db, automaton, session_id)
                    processor = BenchmarkProcessor(
                        self._db, automaton, tracking_engine, env, session_facts, metrics, signal_source, sink,
                    )

                    def report_progress() -> None:
                        nonlocal completed_turns
                        completed_turns += 1
                        on_progress(completed_turns)

                    warning = await processor.run_session(session_id, run, report_progress=report_progress)
                    if warning is not None:
                        warnings.append(warning)

                    if isinstance(signal_source, BatchSignalSource):
                        self._db.add_benchmark_run_batch_segments(run['id'], signal_source.batch_segments)

                self._calculate_and_save_results(run)

            return ("; ".join(warnings) if warnings else None), None

        return work

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

    def start_job(self, username: str, project_name: str, state_key: str, strategy: str) -> int:
        """Pure aggregation over per-session BenchmarkRuns; ephemeral since
        nothing here needs persisting. Sub-runs are found/launched via the
        *persisted* queue, never this one, to avoid a job waiting on its own queue."""
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown benchmark run strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")

        session_ids = sorted(self._db.get_session_ids_with_expected_state(username, project_name, state_key))
        sub_run_ids: list[int] = []
        for session_id in session_ids:
            candidates = [run for run in self.list_runs(project_name, session_id) if run['strategy'] == strategy]
            fresh = candidates[0] if candidates and not candidates[0]['stale'] else None
            if fresh is not None:
                sub_run_ids.append(fresh['id'])
            else:
                new_run = self.create_run(username, project_name, session_id, strategy)
                sub_run_ids.append(new_run['id'])

        async def work(on_progress: OnProgress) -> tuple[str | None, str | None]:
            completed = 0
            for run_id in sub_run_ids:
                final_run = await self._wait_for_run(run_id)
                if final_run['status'] != 'completed':
                    raise RuntimeError(f"Sub-run {run_id} for state {state_key!r} failed: {final_run['error']}")
                completed += 1
                on_progress(completed)

            result = self._aggregate_signal_accuracy(sub_run_ids, state_key)
            return None, json.dumps(result)

        return self._ephemeral_jobs.submit(
            kind='state_aggregation', reference_id=None, total=len(sub_run_ids), work=work,
        )

    def start_users_aggregation_job(self, project_name: str, strategy: str) -> int:
        """The "Users" branch's root aggregation — a simple mean across
        one whole-project-scope run per user, same ephemeral-job pattern
        as start_job/_aggregate_signal_accuracy above."""
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Unknown benchmark run strategy: {strategy!r}. Must be one of {VALID_STRATEGIES}.")

        sessions = self._db.list_chat_sessions(None, project_name, type=None)
        usernames = sorted({row['username'] for row in sessions if row['labeled']})

        sub_run_ids: list[int] = []
        for username in usernames:
            candidates = [
                run for run in self.list_runs(project_name, None, username) if run['strategy'] == strategy
            ]
            fresh = candidates[0] if candidates and not candidates[0]['stale'] else None
            if fresh is not None:
                sub_run_ids.append(fresh['id'])
            else:
                new_run = self.create_run(username, project_name, None, strategy)
                sub_run_ids.append(new_run['id'])

        async def work(on_progress: OnProgress) -> tuple[str | None, str | None]:
            completed = 0
            for run_id in sub_run_ids:
                final_run = await self._wait_for_run(run_id)
                if final_run['status'] != 'completed':
                    raise RuntimeError(f"Sub-run {run_id} for users aggregation failed: {final_run['error']}")
                completed += 1
                on_progress(completed)

            result = self._aggregate_users_mean(sub_run_ids)
            return None, json.dumps(result)

        return self._ephemeral_jobs.submit(
            kind='users_aggregation', reference_id=None, total=len(sub_run_ids), work=work,
        )

    def get_job_status(self, job_id: int) -> dict | None:
        return self._ephemeral_jobs.get(job_id)

    async def _wait_for_run(self, run_id: int, poll_interval: float = 0.2) -> dict:
        while True:
            run = self.get_run(run_id)
            if run['status'] in ('completed', 'failed'):
                return run
            await asyncio.sleep(poll_interval)

    def _aggregate_signal_accuracy(self, sub_run_ids: list[int], state_key: str) -> dict:
        observations: list = []
        for run_id in sub_run_ids:
            run = self._db.get_benchmark_run(run_id)
            data = build_benchmark_run_data(self._db, run)
            observations.extend(BenchmarkObservationBuilder(BenchmarkConfiguration()).build(data))

        filtered = tuple(o for o in observations if o.expected_state == state_key)
        return _serialize_metric_result(SignalAccuracyMetric().calculate(filtered))

    def _aggregate_users_mean(self, sub_run_ids: list[int]) -> list[dict]:
        results_by_name: dict[str, list[dict]] = {}
        for run_id in sub_run_ids:
            run = self._db.get_benchmark_run(run_id)
            for result in run['results'] or []:
                results_by_name.setdefault(result['name'], []).append(result)

        return [
            {
                'name': name,
                'value': sum(result['value'] for result in results) / len(results),
                'mean': None,
                'median': None,
                'standard_deviation': None,
                'minimum': None,
                'maximum': None,
                'sample_count': sum(result['sample_count'] for result in results),
                'components': {},
            }
            for name, results in results_by_name.items()
        ]
