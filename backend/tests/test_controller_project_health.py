"""HTTP-level behavior of a broken project's design view (see
EditProjectController/SettingsController): the automaton-derived
endpoints degrade to a clean 409 (code="project_broken") instead of a
500/400, the file endpoints keep working (that's the only way to fix a
broken project), and resuming a manually-paused-but-broken project is
rejected the same way.
"""
from __future__ import annotations

from http import HTTPStatus

import pytest

from conftest import parse_sse_result

pytestmark = pytest.mark.regression

VALID_YML = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"
BROKEN_YML = "not: [valid, yaml: at all"


def _upload(client, project_id: str, index_yml: str) -> None:
    yml = f"project:\n  id: {project_id}\n{index_yml}"
    response = client.post(
        "/api/projects/upload", content=yml.encode(), headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200, response.text
    parse_sse_result(response)


def _break_project(app, app_db, project_id: str) -> None:
    """Corrupts the already-uploaded (and thus already-cached) project's
    stored index.yml in place, then drops the AutomatonLoader's own
    cached Automaton for it — a real process would only ever see this on
    a fresh cache miss, never on a revision it already built successfully
    earlier in its own lifetime (see test_project_health.py's own helper)."""
    from db.models import Archive
    revision = app_db.get_project_published_revision(project_id)
    Archive.update(content=BROKEN_YML.encode("utf-8")).where(
        (Archive.project == project_id) & (Archive.archive_name == "index.yml") & (Archive.revision == revision)
    ).execute()
    app.state.chat_service._project_service._manager._automaton_loader.invalidate_cache(project_id)


def test_automaton_derived_endpoints_return_409_project_broken(client, app, app_db):
    _upload(client, "broken", VALID_YML)
    _break_project(app, app_db, "broken")

    for path in (
        "/api/projects/broken/project", "/api/projects/broken/states", "/api/projects/broken/graph",
        "/api/projects/broken/signals", "/api/projects/broken/env-keys", "/api/projects/broken/identifiers",
    ):
        response = client.get(path)
        assert response.status_code == HTTPStatus.CONFLICT, (path, response.text)
        assert response.json()["error"]["code"] == "project_broken", (path, response.text)


def test_put_field_endpoints_return_409_project_broken(client, app, app_db):
    _upload(client, "broken", VALID_YML)
    _break_project(app, app_db, "broken")

    response = client.put("/api/projects/broken/states/a/ui-label", json={"value": "New label"})

    assert response.status_code == HTTPStatus.CONFLICT, response.text
    assert response.json()["error"]["code"] == "project_broken"


def test_file_endpoints_still_work_on_a_broken_project(client, app, app_db):
    _upload(client, "broken", VALID_YML)
    _break_project(app, app_db, "broken")

    files = client.get("/api/projects/broken/files")
    assert files.status_code == 200, files.text
    assert "index.yml" in files.json()["files"]

    content = client.get("/api/projects/broken/files/index.yml")
    assert content.status_code == 200, content.text

    fixed = f"project:\n  id: broken\n{VALID_YML}"
    saved = client.put("/api/projects/broken/files/index.yml", content=fixed.encode())
    assert saved.status_code == 200, saved.text

    # The fix just saved builds again — the automaton-derived endpoints
    # must recover without any further action.
    recovered = client.get("/api/projects/broken/project")
    assert recovered.status_code == 200, recovered.text


def test_a_session_pinned_to_an_old_now_broken_revision_is_flagged_unsupported(client, app, app_db):
    """The project itself stays healthy (a newer revision is published
    and builds fine) — only the one session still pinned to the older,
    since-superseded revision that broke is affected."""
    _upload(client, "flaky", VALID_YML)
    session = client.get("/api/chat/session").json()
    assert session["project_revision"] == 0

    # A second, still-valid revision gets published — the session above
    # keeps running against revision 0, exactly as before.
    updated_yml = f"project:\n  id: flaky\ninit-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi again\n"
    saved = client.put("/api/projects/flaky/files/index.yml", content=updated_yml.encode())
    assert saved.status_code == 200, saved.text
    published = client.post("/api/projects/flaky/publish", json={"remap_to": None})
    assert published.status_code == 200, published.text
    assert published.json()["published_revision"] == 1

    # Now revision 0 alone breaks — the currently published one (1) is untouched.
    from db.models import Archive
    Archive.update(content=BROKEN_YML.encode("utf-8")).where(
        (Archive.project == "flaky") & (Archive.archive_name == "index.yml") & (Archive.revision == 0)
    ).execute()
    app.state.chat_service._project_service._manager._automaton_loader.invalidate_cache("flaky")

    sessions = client.get("/api/projects/flaky/sessions").json()
    row = next(s for s in sessions if s["id"] == session["id"])
    assert row["unsupported_revision"] is True

    runtime_status = client.get("/api/settings/projects/runtime-status").json()["projects"]
    flaky_row = next(p for p in runtime_status if p["id"] == "flaky")
    assert flaky_row["status"] == "running"
    assert flaky_row["broken"] == {"published": None, "draft": None}


def test_resume_is_rejected_for_a_project_whose_published_revision_is_broken(client, app, app_db):
    _upload(client, "solo", VALID_YML)
    paused = client.put("/api/projects/solo/pause")
    assert paused.status_code == 200, paused.text
    _break_project(app, app_db, "solo")

    response = client.put("/api/projects/solo/resume")

    assert response.status_code == HTTPStatus.CONFLICT, response.text
    assert response.json()["error"]["code"] == "project_broken"
