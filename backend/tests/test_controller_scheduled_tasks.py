"""GET /api/settings/tasks (Db.list_tasks, Settings > Manage services >
Scheduler)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.contract


@pytest.mark.regression
def test_scheduled_tasks_lists_every_task_soonest_first(client, app_db, hello_project):
    app_db.create_task(
        "task-2", "on_enter", "user", hello_project, datetime(2030, 1, 2, tzinfo=timezone.utc),
        {"secret": "internal"}, "Second task", "Runs second",
    )
    app_db.create_task(
        "task-1", "on_enter", "user", hello_project, datetime(2030, 1, 1, tzinfo=timezone.utc),
        {"secret": "internal"}, "First task", "Runs first",
    )

    response = client.get("/api/settings/tasks")

    assert response.status_code == 200
    tasks = response.json()["tasks"]
    assert [t["key"] for t in tasks] == ["task-1", "task-2"]
    first = tasks[0]
    assert first["ui_label"] == "First task"
    assert first["ui_description"] == "Runs first"
    assert first["status"] == "pending"
    assert first["project_id"] == hello_project
    assert first["username"] == "user"
    assert "payload" not in first


@pytest.mark.contract
def test_scheduled_tasks_empty_when_none_pending(client):
    response = client.get("/api/settings/tasks")

    assert response.status_code == 200
    assert response.json()["tasks"] == []
