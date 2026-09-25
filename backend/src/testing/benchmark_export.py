from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from metrics.metrics_framework.benchmark_metrics.calculator import BenchmarkCalculator
from metrics.metrics_framework.benchmark_metrics.metrics import SignalAccuracyMetric

if TYPE_CHECKING:
    from testing.testing_service import TestingService


SIGNAL_ACCURACY = SignalAccuracyMetric().name


class BenchmarkExport:

    def __init__(self, service: "TestingService", project_id: str, strategy: str) -> None:
        self._service = service
        self._db = service._db
        self._project_id = project_id
        self._strategy = strategy

    def as_dict(self) -> dict:
        automaton = self._service._load_automaton(self._project_id)
        sessions = [row for row in self._db.list_chat_sessions(None, self._project_id, type=None) if row['labeled']]
        return {
            'metrics': self._metrics(),
            'run': {
                'project_id': self._project_id,
                'strategy': self._strategy,
                'exported_at': datetime.now(timezone.utc).isoformat(),
                'sessions': {
                    'results': self._keyed(self._aggregate('sessions', None)),
                    'sessions': {str(row['id']): self._session(row) for row in sessions},
                },
                'states': {
                    'results': self._single(self._aggregate('all_states', None)),
                    'states': {
                        state.key: {'results': self._single(self._aggregate('state', state.key))}
                        for state in automaton.states.values() if state.key != ""
                    },
                },
                'users': {
                    'results': self._keyed(self._aggregate('users', None)),
                    'users': {
                        username: {
                            'results': self._keyed(self._aggregate('user_sessions', username)),
                            'sessions': [str(row['id']) for row in sessions if row['username'] == username],
                        }
                        for username in sorted({row['username'] for row in sessions})
                    },
                },
                'signals': {
                    'results': self._single(self._aggregate('all_signals', None)),
                    'signals': {
                        signal.name: {
                            'label': signal.ui_label,
                            'results': self._single(self._aggregate('signal', signal.name)),
                        }
                        for signal in automaton.signals
                    },
                },
            },
        }

    def _metrics(self) -> dict:
        return {
            metric.name: {
                'label': metric.ui_label,
                'description': metric.ui_description,
                'scope': sorted(metric.scope),
            }
            for metric in BenchmarkCalculator(self._db, None, self._project_id).default_metrics()
        }

    def _aggregate(self, kind: str, target: str | None) -> dict | list[dict] | None:
        return self._service.get_aggregate_result(self._project_id, kind, target, self._strategy)

    def _session(self, row: dict) -> dict:
        run = next(
            (
                run for run in self._service.list_runs(self._project_id, int(row['id']))
                if run['strategy'] == self._strategy and run['status'] == 'completed'
            ),
            None,
        )
        return {
            'title': row['title'],
            'username': row['username'],
            'datetime_start': row['datetime_start'],
            'datetime_end': row['datetime_end'],
            'start_state': row['start_state'],
            'end_state': row['end_state'],
            'turns': sum(1 for message in self._db.get_messages(int(row['id'])) if message['role'] == 'user'),
            'stale': run['stale'] if run is not None else None,
            'results': self._keyed(run['results'] if run is not None else None),
        }

    @staticmethod
    def _statistics(result: dict) -> dict:
        return {key: value for key, value in result.items() if key != 'name'}

    def _keyed(self, results: list[dict] | None) -> dict | None:
        if results is None:
            return None
        return {result['name']: self._statistics(result) for result in results}

    def _single(self, result: dict | None) -> dict | None:
        if result is None:
            return None
        return {SIGNAL_ACCURACY: self._statistics(result)}
