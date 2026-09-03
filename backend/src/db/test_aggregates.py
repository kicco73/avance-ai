from __future__ import annotations

import json

from .models import TestAggregateResult


class TestAggregateMixin:

    def upsert_test_aggregate_result(
        self, project_id: str, revision: int, project_draft_edit_count: int,
        kind: str, target: str | None, strategy: str, results: str,
    ) -> None:
        target = target or ''
        row = TestAggregateResult.get_or_none(
            (TestAggregateResult.project_id == project_id) & (TestAggregateResult.revision == revision)
            & (TestAggregateResult.project_draft_edit_count == project_draft_edit_count)
            & (TestAggregateResult.kind == kind) & (TestAggregateResult.target == target)
            & (TestAggregateResult.strategy == strategy)
        )
        if row is None:
            TestAggregateResult.create(
                project_id=project_id, revision=revision, project_draft_edit_count=project_draft_edit_count,
                kind=kind, target=target, strategy=strategy, results=results,
            )
        else:
            row.results = results
            row.save()

    def find_test_aggregate_result(
        self, project_id: str, kind: str, target: str | None, strategy: str, project_draft_edit_count: int,
    ) -> dict | None:
        revision = self._current_revision(project_id)
        row = TestAggregateResult.get_or_none(
            (TestAggregateResult.project_id == project_id) & (TestAggregateResult.revision == revision)
            & (TestAggregateResult.project_draft_edit_count == project_draft_edit_count)
            & (TestAggregateResult.kind == kind) & (TestAggregateResult.target == (target or ''))
            & (TestAggregateResult.strategy == strategy)
        )
        return json.loads(row.results) if row is not None else None

    def list_test_aggregate_results(self, project_id: str, revision: int | None = None) -> list[dict]:
        if revision is None:
            revision = self._current_revision(project_id)
        query = TestAggregateResult.select().where(
            (TestAggregateResult.project_id == project_id)
            & (TestAggregateResult.revision == revision)
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

    def delete_test_aggregate_results(self, project_id: str) -> None:
        TestAggregateResult.delete().where(TestAggregateResult.project_id == project_id).execute()
