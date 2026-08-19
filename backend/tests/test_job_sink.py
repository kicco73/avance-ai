from __future__ import annotations

import pytest

from jobs import InMemoryJobSink, PersistedJobSink

pytestmark = pytest.mark.contract


@pytest.fixture(params=["persisted", "in_memory"])
def sink(request, db):
    """Both JobSink implementations must satisfy the exact same contract
    — PersistedJobSink is a thin delegate to Db's own job methods, so
    this also exercises those directly, without a separate db-level test
    file duplicating the same assertions."""
    if request.param == "persisted":
        return PersistedJobSink(db)
    return InMemoryJobSink()


def test_create_returns_a_pending_job(sink):
    job_id = sink.create("some_kind", reference_id=7, total=10)

    job = sink.get(job_id)

    assert job["kind"] == "some_kind"
    assert job["reference_id"] == 7
    assert job["status"] == "pending"
    assert job["progress_current"] == 0
    assert job["progress_total"] == 10
    assert job["finished_at"] is None
    assert job["error"] is None
    assert job["result"] is None


def test_create_allows_no_reference_id(sink):
    job_id = sink.create("ephemeral_kind", reference_id=None, total=0)

    assert sink.get(job_id)["reference_id"] is None


def test_set_running_updates_status(sink):
    job_id = sink.create("k", None, 1)

    sink.set_running(job_id)

    assert sink.get(job_id)["status"] == "running"


def test_set_progress_updates_progress_current(sink):
    job_id = sink.create("k", None, 10)

    sink.set_progress(job_id, 4)

    assert sink.get(job_id)["progress_current"] == 4


def test_set_completed_marks_status_and_stores_result(sink):
    job_id = sink.create("k", None, 1)

    sink.set_completed(job_id, result='{"ok": true}')

    job = sink.get(job_id)
    assert job["status"] == "completed"
    assert job["finished_at"] is not None
    assert job["result"] == '{"ok": true}'
    assert job["error"] is None


def test_set_completed_with_a_warning_keeps_status_completed(sink):
    job_id = sink.create("k", None, 1)

    sink.set_completed(job_id, warning="something looked off")

    job = sink.get(job_id)
    assert job["status"] == "completed"
    # No dedicated column for a non-fatal warning — it rides on `error`,
    # distinguished from a real failure by status still being completed.
    assert job["error"] == "something looked off"


def test_set_failed_marks_status_and_stores_error(sink):
    job_id = sink.create("k", None, 1)

    sink.set_failed(job_id, "boom")

    job = sink.get(job_id)
    assert job["status"] == "failed"
    assert job["finished_at"] is not None
    assert job["error"] == "boom"


def test_get_returns_none_for_an_unknown_job(sink):
    assert sink.get(999999) is None


def test_list_returns_every_job_when_kind_is_omitted(sink):
    id_a = sink.create("kind_a", None, 1)
    id_b = sink.create("kind_b", None, 1)

    ids = {job["id"] for job in sink.list()}

    assert ids == {id_a, id_b}


def test_list_filters_by_kind(sink):
    id_a = sink.create("kind_a", None, 1)
    sink.create("kind_b", None, 1)

    jobs = sink.list(kind="kind_a")

    assert [job["id"] for job in jobs] == [id_a]
