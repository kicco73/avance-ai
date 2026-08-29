from __future__ import annotations

import asyncio

import pytest

from jobs import CancelableJob

pytestmark = pytest.mark.contract


class _FakeCancelableJob(CancelableJob):
    def __init__(self, key: str) -> None:
        super().__init__(key=key, username="test")

    def _prepare(self) -> tuple[int, tuple[CancelableJob, ...]]:
        return 1, ()

    @property
    def result(self) -> str | None:
        return None

    async def _run_next_step(self) -> None:
        return


def test_abort_all_jobs_cancels_only_jobs_still_in_flight(client, hello_project):
    """The square "run all" button's stop action must cancel every job
    genuinely still running, but never retroactively flip an already
    completed one to 'aborted' — see TestService.abort_all_jobs()."""
    test_service = client.app.state.test_service

    done = _FakeCancelableJob("batch:done")
    done.prepare()
    asyncio.run(done.run_next_step())
    assert done.is_done()

    in_flight = _FakeCancelableJob("batch:in-flight")
    in_flight.prepare()

    test_service._jobs_by_key["batch:done"] = done
    test_service._jobs_by_key["batch:in-flight"] = in_flight

    test_service.abort_all_jobs()

    assert not done.is_aborted()
    assert in_flight.is_aborted()


def test_delete_all_test_jobs_endpoint_calls_abort_all_jobs(client, hello_project, monkeypatch):
    test_service = client.app.state.test_service
    calls = []
    monkeypatch.setattr(test_service, "abort_all_jobs", lambda: calls.append(1))

    response = client.delete(f"/api/projects/{hello_project}/tests/jobs")

    assert response.status_code == 200, response.text
    assert calls == [1]
