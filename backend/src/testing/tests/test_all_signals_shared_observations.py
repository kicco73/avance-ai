"""Regression test for AllSignalsAggregationJob: it fans out into one
SignalAggregationJob per project signal, all resolving the exact same
session/run ids. Before the fix, each one independently rebuilt the
(expensive, DB-round-trip-heavy) observation list for every run id it
touched, so a project with M signals and N sessions did M*N rebuilds of
data that's identical regardless of which signal is being read out of it —
see SignalAggregationJob's own observations_cache.
"""
from __future__ import annotations

import io
import time
import zipfile

import pytest

from conftest import chat_turn, enter_chat, installed_skill, parse_sse_result, session_of

from jobs.job_queue import JobQueue
from testing.jobs.base import _AggregationJob

REACHES_INTO = {
    "_observations_for_run": "built-once is a performance property; a build and a cache hit are identical to every caller",
}

pytestmark = pytest.mark.contract

SIGNAL_NAMES = ("foo", "bar", "baz")

_INDEX_YML = """avance-version: "1.7.0"

init-action:
  target: Hello

signals:
""" + "".join(
    f"  {name}:\n    ui-label: {name.title()}\n    definition: |\n      A 0-100 value.\n" for name in SIGNAL_NAMES
) + """
states:
  Hello:
    contextual-prompt: |
      Ignore all user input. You always respond "hello, world!".
project:
  ui-label: Signals, world!
  id: signals_world
"""


@pytest.fixture
def signals_project(client) -> str:
    """Same shape as the bundled "Hello world" sample, plus three declared
    signals — what makes the all-signals route fan out at all."""
    installed_skill("avance_platform")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("index.yml", _INDEX_YML)
    response = client.post(
        "/api/skills/platform/projects/upload", content=buffer.getvalue(),
        headers={"Content-Type": "application/zip"},
    )
    assert response.status_code == 200, response.text
    project_id = parse_sse_result(response)["project_id"]
    assert client.post(f"/api/core/projects/{project_id}/activate").status_code == 200
    assert client.post(f"/api/skills/platform/projects/{project_id}/publish", json={}).status_code == 200
    return project_id


def _wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _make_completed_run(client, project_id):
    session_id = session_of(enter_chat(client, project_id))
    chat_turn(client, session_id, "hi")
    client.put(f"/api/skills/platform/sessions/{session_id}/labeled", json={"labeled": True})

    leaf_run = client.post(
        f"/api/skills/testing/projects/{project_id}/tests", json={"session_id": session_id, "strategy": "turn_by_turn"},
    ).json()
    deadline = time.monotonic() + 5.0
    run = client.get(f"/api/skills/testing/projects/{project_id}/tests/{leaf_run['id']}").json()
    while time.monotonic() < deadline and run["status"] not in ("completed", "failed"):
        time.sleep(0.05)
        run = client.get(f"/api/skills/testing/projects/{project_id}/tests/{leaf_run['id']}").json()
    assert run["status"] == "completed", run
    return session_id, leaf_run["id"]


def _count_builds(monkeypatch, build_delay: float = 0.0) -> list[int]:
    """One entry per observation build. "Built once, not M*N times" is a
    performance property: nothing a caller can observe tells a build apart
    from a cache hit, so the build itself is counted where it happens.
    `build_delay` widens the window a concurrent sibling can race in —
    also nothing a caller can reach."""
    builds: list[int] = []
    original = _AggregationJob._observations_for_run

    def counting(self, run_id):
        builds.append(run_id)
        time.sleep(build_delay)
        return original(self, run_id)

    monkeypatch.setattr(_AggregationJob, "_observations_for_run", counting)
    return builds


def _all_signals_result_ready(client, project_id):
    response = client.get(
        f"/api/skills/testing/projects/{project_id}/aggregations/result",
        params={"kind": "all_signals", "strategy": "turn_by_turn"},
    )
    return response.status_code == 200


def test_all_signals_aggregation_builds_each_runs_observations_only_once(monkeypatch, client, signals_project):
    _, run_id = _make_completed_run(client, signals_project)
    builds = _count_builds(monkeypatch)

    response = client.post(
        f"/api/skills/testing/projects/{signals_project}/aggregations/signals", json={"strategy": "turn_by_turn"},
    )
    assert response.status_code == 200, response.text

    assert _wait_until(lambda: _all_signals_result_ready(client, signals_project))
    assert builds == [run_id]


def test_all_signals_aggregation_coalesces_concurrent_observation_building(monkeypatch, client, signals_project):
    """With several worker threads (see JobQueue's max_concurrent) all the
    signal jobs' first step can genuinely run at once — a plain shared
    dict alone doesn't stop them from each seeing a miss and rebuilding in
    parallel before any of them finishes writing. Slows the real build
    down to widen that race window, and asserts it still only ever runs
    once — see SignalAggregationJob.SharedObservationsCache."""
    _, run_id = _make_completed_run(client, signals_project)
    builds = _count_builds(monkeypatch, build_delay=0.2)

    testing_service = client.app.state.testing_service
    monkeypatch.setattr(
        testing_service, "_job_queue",
        JobQueue(max_concurrent=4, broadcaster=client.app.state.progress_broadcaster),
    )

    response = client.post(
        f"/api/skills/testing/projects/{signals_project}/aggregations/signals", json={"strategy": "turn_by_turn"},
    )
    assert response.status_code == 200, response.text

    assert _wait_until(lambda: _all_signals_result_ready(client, signals_project), timeout=5.0)
    assert builds == [run_id]
