from __future__ import annotations

import json

from .models import BenchmarkAggregateResult


class BenchmarkAggregateMixin:

    def upsert_benchmark_aggregate_result(
        self, project_name: str, revision: int, project_draft_edit_count: int,
        kind: str, target: str | None, strategy: str, results: str,
    ) -> None:
        target = target or ''
        row = BenchmarkAggregateResult.get_or_none(
            (BenchmarkAggregateResult.project_name == project_name) & (BenchmarkAggregateResult.revision == revision)
            & (BenchmarkAggregateResult.project_draft_edit_count == project_draft_edit_count)
            & (BenchmarkAggregateResult.kind == kind) & (BenchmarkAggregateResult.target == target)
            & (BenchmarkAggregateResult.strategy == strategy)
        )
        if row is None:
            BenchmarkAggregateResult.create(
                project_name=project_name, revision=revision, project_draft_edit_count=project_draft_edit_count,
                kind=kind, target=target, strategy=strategy, results=results,
            )
        else:
            row.results = results
            row.save()

    def find_benchmark_aggregate_result(
        self, project_name: str, kind: str, target: str | None, strategy: str, project_draft_edit_count: int,
    ) -> dict | None:
        revision = self._current_revision(project_name)
        row = BenchmarkAggregateResult.get_or_none(
            (BenchmarkAggregateResult.project_name == project_name) & (BenchmarkAggregateResult.revision == revision)
            & (BenchmarkAggregateResult.project_draft_edit_count == project_draft_edit_count)
            & (BenchmarkAggregateResult.kind == kind) & (BenchmarkAggregateResult.target == (target or ''))
            & (BenchmarkAggregateResult.strategy == strategy)
        )
        return json.loads(row.results) if row is not None else None

    def list_benchmark_aggregate_results(self, project_name: str, revision: int | None = None) -> list[dict]:
        if revision is None:
            revision = self._current_revision(project_name)
        query = BenchmarkAggregateResult.select().where(
            (BenchmarkAggregateResult.project_name == project_name)
            & (BenchmarkAggregateResult.revision == revision)
        )
        return [
            {
                'kind': row.kind,
                'target': row.target or None,
                'strategy': row.strategy,
                'results': json.loads(row.results),
            }
            for row in query
        ]

    def delete_benchmark_aggregate_results(self, project_name: str) -> None:
        BenchmarkAggregateResult.delete().where(BenchmarkAggregateResult.project_name == project_name).execute()
