"""PersistedScheduler's own contract, with a stub Task: the Task table is
its queue — submit inserts, the loop claims atomically, settlement and
cancel update the row — and a new scheduler over the same database
just continues: pending rows at their time, rows a dead process left
`dispatched` right away, rows nobody can hydrate as `failed`."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from conftest import NullBroadcaster
from db import Db
from db.models import Task as TaskRow
from job.persisted_scheduler import PersistedScheduler
from jobs import CancelableJob, Task
from jobs.job_queue import JobQueue

pytestmark = pytest.mark.contract


def _wait_until(predicate, timeout=3.0, interval=0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class StubTask(Task):
    """Hibernates to {"value": ...}; runs by recording that value on a
    shared per-test `sink` (the hydrator closes over it), or failing on request."""

    TYPE = "stub"

    def __init__(self, key: str, username: str, payload: dict[str, Any], sink: list) -> None:
        super().__init__(key, username)
        self._payload = payload
        self._sink = sink

    @property
    def project_id(self) -> str:
        return "p"

    @property
    def ui_label(self) -> str:
        return f"stub {self._payload.get('value')}"

    @property
    def ui_description(self) -> str:
        return "records its value"

    def dehydrate(self) -> dict[str, Any]:
        return dict(self._payload)

    def _prepare(self):
        return 1, ()

    @property
    def result(self):
        return None

    async def _run_next_step(self) -> None:
        if self._payload.get("fail"):
            raise RuntimeError("boom")
        self._sink.append(self._payload["value"])


def _hydrators(sink: list) -> dict:
    return {StubTask.TYPE: lambda key, username, payload: StubTask(key, username, payload, sink)}


@pytest.fixture
def file_db(tmp_path) -> Db:
    # File-backed, not :memory: — the scheduler and queue threads each
    # open their own connection (see conftest.app_db's docstring).
    instance = Db(f"sqlite:///{tmp_path / 'tasks.db'}")
    instance.get_or_create_user("test", "sub-user", "user", "user", None)
    instance.ensure_project("p")
    return instance


_live_schedulers: list[PersistedScheduler] = []


@pytest.fixture(autouse=True)
def _stop_schedulers():
    """db.py's `database` Proxy is process-global and rebound by every
    Db(...) — a scheduler thread left polling from a previous test would
    claim *this* test's rows. Every scheduler built here is stopped on teardown."""
    yield
    while _live_schedulers:
        _live_schedulers.pop().stop()


def _make(
    file_db: Db, sink: list, *, start: bool = True, hydrators: dict | None = None, lease_seconds: float = 600.0,
) -> PersistedScheduler:
    queue = JobQueue(max_concurrent=1, broadcaster=NullBroadcaster())
    scheduler = PersistedScheduler(
        queue, file_db, hydrators if hydrators is not None else _hydrators(sink),
        poll_interval_seconds=0.2, lease_seconds=lease_seconds,
    )
    _live_schedulers.append(scheduler)
    if start:
        scheduler.start()
    return scheduler


def _status(file_db: Db, key: str) -> str:
    return file_db.get_task(key)["status"]


def _future(hours: float = 1) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def _due_row(file_db: Db, key: str, value: int, type: str = "stub") -> None:
    file_db.create_task(key, type, "user", "p", datetime.now(timezone.utc) - timedelta(minutes=1), {"value": value}, "l", "d")


def _dispatched(key: str, at: datetime | None) -> None:
    TaskRow.update(status="dispatched", dispatched_at=at).where(TaskRow.key == key).execute()


def test_a_future_task_becomes_a_pending_row_that_does_not_run_and_cancelling_it_marks_it_canceled(file_db):
    sink: list = []
    scheduler = _make(file_db, sink)
    task = StubTask("stub:1", "user", {"value": 1}, sink)

    scheduler.submit(task, timestamp=_future())

    row = file_db.get_task("stub:1")
    assert row["status"] == "pending"
    assert row["type"] == "stub"
    assert row["username"] == "user"
    assert row["project_id"] == "p"
    assert row["payload"] == {"value": 1}
    assert row["ui_label"] == "stub 1"
    assert row["ui_description"] == "records its value"
    time.sleep(0.3)
    assert sink == []

    scheduler.cancel(task)
    assert _status(file_db, "stub:1") == "canceled"
    assert file_db.next_task_due_at() is None


def test_a_due_task_is_claimed_run_and_settled_done_while_a_failing_one_is_recorded_failed_with_its_error(file_db):
    sink: list = []
    scheduler = _make(file_db, sink)

    scheduler.submit(StubTask("stub:1", "user", {"value": 1}, sink), timestamp=datetime.now(timezone.utc) - timedelta(seconds=1))
    scheduler.submit(StubTask("stub:2", "user", {"value": 2, "fail": True}, sink))

    assert _wait_until(lambda: _status(file_db, "stub:1") == "done")
    assert sink == [1]
    assert file_db.get_task("stub:1")["settled_at"] is not None
    assert _wait_until(lambda: _status(file_db, "stub:2") == "failed")
    assert file_db.get_task("stub:2")["error"] == "boom"


def test_a_task_due_soon_runs_at_its_time_without_waiting_for_a_poll(file_db):
    sink: list = []
    scheduler = _make(file_db, sink)
    # poll_interval is 0.2s here; make the due time land well before the
    # *second* poll so a wake-up on the exact due time is what makes it.
    scheduler.submit(StubTask("stub:1", "user", {"value": 1}, sink), timestamp=datetime.now(timezone.utc) + timedelta(seconds=0.5))

    assert not _wait_until(lambda: sink == [1], timeout=0.3)
    assert _wait_until(lambda: sink == [1], timeout=1.0)


def test_a_non_task_job_or_a_task_of_an_unregistered_type_is_refused_leaving_no_row(file_db):
    class Plain(CancelableJob):
        def __init__(self):
            super().__init__("plain", "user")

        def _prepare(self):
            return 1, ()

        @property
        def result(self):
            return None

        async def _run_next_step(self):
            pass

    class Other(StubTask):
        TYPE = "other"

    scheduler = _make(file_db, [])
    with pytest.raises(TypeError, match="jobs.Task"):
        scheduler.submit(Plain())
    with pytest.raises(ValueError, match="no hydrator is registered"):
        scheduler.submit(Other("other:1", "user", {"value": 1}, []))
    assert file_db.list_tasks() == []


def test_nothing_is_claimed_before_start(file_db):
    sink: list = []
    scheduler = _make(file_db, sink, start=False)
    scheduler.submit(StubTask("stub:1", "user", {"value": 1}, sink), timestamp=datetime.now(timezone.utc) - timedelta(days=1))

    time.sleep(0.3)
    assert _status(file_db, "stub:1") == "pending"
    assert sink == []

    scheduler.start()
    assert _wait_until(lambda: _status(file_db, "stub:1") == "done")


class TestTheTableIsTheQueue:

    def test_a_new_scheduler_runs_what_the_previous_one_left_pending(self, file_db):
        first = _make(file_db, [], start=False)
        first.submit(StubTask("stub:1", "user", {"value": 1}, []), timestamp=_future())
        # "Restart": the process that accepted the task is gone; the row
        # comes due meanwhile.
        TaskRow.update(run_at=datetime.utcnow() - timedelta(seconds=1)).where(TaskRow.key == "stub:1").execute()
        sink: list = []

        _make(file_db, sink)

        assert _wait_until(lambda: _status(file_db, "stub:1") == "done")
        assert sink == [1]
        assert [row["key"] for row in file_db.list_tasks()] == ["stub:1"]

    def test_a_claim_older_than_the_lease_or_predating_the_lease_column_runs_again_while_a_fresh_one_is_left_alone(self, file_db):
        """A dead process's claim: older than the lease, no settlement.
        Another live instance (or a worker of this one) running a fresh
        claim must not be second-guessed just because it is dispatched."""
        _due_row(file_db, "stub:stale", 7)
        _dispatched("stub:stale", datetime.utcnow() - timedelta(hours=1))
        _due_row(file_db, "stub:legacy", 8)
        _dispatched("stub:legacy", None)
        _due_row(file_db, "stub:fresh", 9)
        _dispatched("stub:fresh", datetime.utcnow())
        sink: list = []

        _make(file_db, sink)

        assert _wait_until(lambda: _status(file_db, "stub:stale") == "done" and _status(file_db, "stub:legacy") == "done")
        time.sleep(0.6)
        assert sorted(sink) == [7, 8]
        assert _status(file_db, "stub:fresh") == "dispatched"

    def test_a_stale_claim_is_recovered_by_a_running_scheduler_too_not_only_at_boot(self, file_db):
        sink: list = []
        _make(file_db, sink, lease_seconds=0.3)
        _due_row(file_db, "stub:1", 7)
        _dispatched("stub:1", datetime.utcnow())

        assert _wait_until(lambda: _status(file_db, "stub:1") == "done", timeout=3.0)
        assert sink == [7]

    def test_settled_rows_are_never_run_and_a_row_of_an_unknown_type_is_marked_failed_not_dropped(self, file_db):
        for key, status in (("stub:done", "done"), ("stub:failed", "failed"), ("stub:canceled", "canceled")):
            _due_row(file_db, key, 0)
            file_db.settle_task(key, status)
        _due_row(file_db, "weird:1", 0, type="weird")
        sink: list = []

        _make(file_db, sink)

        assert _wait_until(lambda: _status(file_db, "weird:1") == "failed")
        assert "no hydrator" in file_db.get_task("weird:1")["error"]
        time.sleep(0.3)
        assert sink == []

    def test_a_row_the_hydrator_rejects_is_marked_failed_with_the_reason(self, file_db):
        _due_row(file_db, "stub:1", 1)

        def refuse(key, username, payload):
            raise ValueError("payload from the future")

        _make(file_db, [], hydrators={"stub": refuse})

        assert _wait_until(lambda: _status(file_db, "stub:1") == "failed")
        assert "payload from the future" in file_db.get_task("stub:1")["error"]

    def test_a_row_deleted_behind_the_schedulers_back_never_runs(self, file_db):
        """Nothing is cached: a pending row that vanishes (here by hand,
        in production by a project/user cascade) is simply not there
        when its time comes."""
        sink: list = []
        scheduler = _make(file_db, sink)
        scheduler.submit(StubTask("stub:1", "user", {"value": 1}, sink), timestamp=datetime.now(timezone.utc) + timedelta(seconds=0.4))

        TaskRow.delete().where(TaskRow.key == "stub:1").execute()

        time.sleep(0.8)
        assert sink == []

    def test_deleting_the_project_or_erasing_the_user_cascades_their_pending_tasks_away(self, file_db):
        sink: list = []
        scheduler = _make(file_db, sink)

        scheduler.submit(StubTask("stub:1", "user", {"value": 1}, sink), timestamp=_future())
        assert file_db.list_tasks(project_id="p")
        file_db.erase_user_data("user")
        assert file_db.list_tasks() == []

        file_db.get_or_create_user("test", "sub-user", "user", "user", None)
        scheduler.submit(StubTask("stub:2", "user", {"value": 2}, sink), timestamp=_future())
        assert file_db.list_tasks(project_id="p")
        file_db.delete_archives("p")  # what ProjectManager.delete_project does — drops the Project row
        assert file_db.list_tasks() == []

    def test_two_schedulers_over_one_table_never_run_the_same_row_twice(self, file_db):
        sink: list = []
        for i in range(20):
            _due_row(file_db, f"stub:{i}", i)

        _make(file_db, sink)
        _make(file_db, sink)

        assert _wait_until(lambda: all(row["status"] == "done" for row in file_db.list_tasks()), timeout=5.0)
        assert sorted(sink) == list(range(20))
