from __future__ import annotations

import json
from typing import TYPE_CHECKING

from automaton.automaton import Automaton
from chat.env_for_session import env_for_session
from jobs import CancelableJob
from metrics.metrics_framework.benchmark_metrics.calculator import BenchmarkCalculator
from session import Session
from testing.data import TestDataBuilder
from testing.metrics_provider import TestMetricsProvider
from testing.processor import TestProcessor
from testing.signal_sources import BatchSignalSource, estimate_max_turns_per_call
from tracking.env import Env
from tracking.evaluation_scope import EvaluationScopeBuilder
from tracking.fixed_project_context import FixedProjectContext
from tracking.session_facts import SessionFacts
from tracking.tracking_engine import TestObservationSink, TrackingEngine
from tracking.user_facts import UserFacts

from .serialization import _serialize_metric_result

if TYPE_CHECKING:
    from testing.test_service import TestService


class TestReplayJob(CancelableJob):

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

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
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
            batch = self._pending_batches[0]
            if isinstance(self._signal_source, BatchSignalSource):
                await self._signal_source.prepare_batch(batch)
            for message_id in batch:
                await self._processor.process_message(self._current_session_id, message_id)
            self._pending_batches.pop(0)
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
        data = TestDataBuilder.build(db, current_run)

        if current_run['session_id'] is not None or current_run['username'] is None:
            calculator = BenchmarkCalculator.from_data(data)
        else:
            unfiltered_metrics = BenchmarkCalculator(db, current_run['username'], current_run['project_id']).default_metrics()
            calculator = BenchmarkCalculator.from_data(data, metrics=unfiltered_metrics)

        results = [_serialize_metric_result(result) for result in calculator.calculate_all()]
        db.set_test_results(self._run['id'], json.dumps(results))

    def _prepare_session(self, session_id: int) -> tuple[list[list[int]], str | None]:
        db = self._service._db
        session = db.get_chat_session(session_id)
        if session is None:
            return [], f"session {session_id}: not found, skipped"
        env = self._build_seed_env(session)
        session_facts = SessionFacts(db, FixedProjectContext(project_id=self._run['project_id']))
        user_facts = UserFacts(db)
        metrics = TestMetricsProvider(db, self._run['username'], self._run['project_id'], session_id)
        scope_builder = EvaluationScopeBuilder(env, metrics, session_facts, user_facts, db)
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
        # env_for_session dispatches by session type, built straight from
        # `session` itself — no need to impersonate Session().user for it.
        # Snapshotted into a fresh, detached Env either way: the rest of
        # this replay run must never write back through the session's own
        # (possibly still-live) env.
        source_env = env_for_session(self._service._db, session)
        until = session['datetime_start']
        return Env(stored=source_env.stored(until=until), action_set=source_env.action_set(until=until))

    def _chunk_into_batches(self, message_ids: list[int]) -> list[list[int]]:
        if not issubclass(self._signal_source_cls, BatchSignalSource):
            return [[message_id] for message_id in message_ids]
        max_turns_per_call = estimate_max_turns_per_call(
            len(self._automaton.signals), self._service._ai_service.get_max_output_tokens(),
        )
        return [
            message_ids[i:i + max_turns_per_call]
            for i in range(0, len(message_ids), max_turns_per_call)
        ]
