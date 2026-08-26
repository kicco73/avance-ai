from __future__ import annotations

import pytest

from db import Db

pytestmark = pytest.mark.contract


def test_create_benchmark_run_replaces_a_dead_row_at_the_same_cache_key(db: Db):
    """A prior run with no results and no live job (BenchmarkRunCache's
    own "dead attempt" case, see find()'s docstring) must not block a
    retry at the exact same (session, strategy, edit_count,
    labeling_revision) cache key — create_benchmark_run's own unique
    index would otherwise reject the retry's insert outright. session_id
    and session_labeling_revision must be real (non-null) values here:
    SQL never considers two NULLs equal, so a NULL-keyed duplicate would
    never actually collide against the unique index in the first place."""
    db.ensure_project("proj")
    session_id = db.create_chat_session("user", "proj", revision=0)

    first = db.create_benchmark_run(None, "proj", session_id, "batch", 1, 0, {})
    assert first["results"] is None

    second = db.create_benchmark_run(None, "proj", session_id, "batch", 1, 0, {})

    assert second["results"] is None
    assert db.list_benchmark_runs("proj", session_id) == [second]
