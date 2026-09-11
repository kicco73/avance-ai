"""The benchmark surface: everything LabelProjectView.vue's own
Performance tab drives, and nothing else.

It used to be written into controllers/label_project_controller.py,
which meant a build without `src/testing/` still answered every /tests
route. Here the routes travel with the package that runs them (see
testing/skill.py) — the labelling half of that screen, which annotates
sessions and needs no benchmark at all, stayed behind in the core.
"""
from __future__ import annotations

import json
from http import HTTPStatus
from urllib.parse import quote

from fastapi import HTTPException, Response

from controllers.base_controller import BaseController, delete, get, post, put
from schemas import CreateTestRequest, StateTestRequest
from system.broadcaster import Broadcaster
from system.session import Session
from testing.testing_service import TestingService
from turn.turn_service import TurnService


class TestingController(BaseController):

    def __init__(
        self, testing_service: TestingService, progress_broadcaster: Broadcaster, turn_service: TurnService,
    ) -> None:
        self.testing_service = testing_service
        self.progress_broadcaster = progress_broadcaster
        self.turn_service = turn_service

    @get("/api/skills/testing/projects/{project_id}/tests/metrics", role="supervisor")
    def get_test_metrics(self, project_id: str, session_id: int | None = None):
        """Expert-annotation-vs-actual benchmark metrics for
        `project_id` — every annotated session, or (session_id given)
        just that one. The "Label sessions" view's Performance tab.

        Here rather than with the labelling routes for the reason
        get_test_record spells out below: a literal /tests/<word> and the
        /tests/{test_id} wildcard must be registered by the same
        controller, or which one answers depends on the order two
        controllers happened to be collected in.
        """
        return self.turn_service.get_benchmark_metrics(project_id=project_id, session_id=session_id)

    @delete("/api/skills/testing/projects/{project_id}/tests", role="supervisor")
    def delete_tests(self, project_id: str):
        self.testing_service.reset_cache(project_id)
        return {"success": True}

    @delete("/api/skills/testing/projects/{project_id}/tests/jobs/{job_key}", role="supervisor")
    def delete_test_job(self, project_id: str, job_key: str):
        """Aborts the running job for job_key (the same "<strategy>:<node_id>"
        string the frontend already computes as its SSE cache key) — a
        no-op if nothing is currently tracked under that key."""
        self.testing_service.abort_job(job_key)
        return {"success": True}

    @delete("/api/skills/testing/projects/{project_id}/tests/jobs", role="supervisor")
    def delete_all_test_jobs(self, project_id: str):
        """Aborts every currently tracked, still in-flight job across every
        node — the square "run all" button's own stop action."""
        self.testing_service.abort_all_jobs()
        return {"success": True}

    @post("/api/skills/testing/projects/{project_id}/tests", role="supervisor")
    def post_test(self, project_id: str, req: CreateTestRequest):
        """Creates a Test and submits its replay job, returning
        immediately with status='pending' — or, for a single-session run
        whose exact (project/annotation state, strategy) was already
        replayed to completion, that cached run directly, with no new
        job submitted. TestServiceError is handled globally."""
        username = req.username if req.username is not None else Session().user
        try:
            return self.testing_service.create_run(
                username, project_id, req.session_id, req.strategy,
            )
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/skills/testing/projects/{project_id}/tests/export", role="supervisor")
    def get_test_export(self, project_id: str):
        payload = self.testing_service.export_results(project_id)
        content = json.dumps(payload, indent=2).encode("utf-8")
        encoded_project_id = quote(project_id)
        return Response(
            content=content,
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename=\"tests.json\"; filename*=UTF-8''{encoded_project_id}-tests.json"
            },
        )

    # Named get_test_record, not get_test: inspect.getmembers walks routes
    # alphabetically (see base_controller.py's own docstring), and "get_test"
    # would sort before — and so shadow — the literal get_test_export/
    # get_test_metrics routes above.
    @get("/api/skills/testing/projects/{project_id}/tests/{test_id}", role="supervisor")
    def get_test_record(self, project_id: str, test_id: int):
        """One Test, its domain data merged with its Job's
        lifecycle (status/progress/error/timestamps). TestServiceError
        (404 for an unknown test_id) is handled globally."""
        return self.testing_service.get_run(test_id)

    @get("/api/skills/testing/projects/{project_id}/tests", role="supervisor")
    def get_tests(self, project_id: str, session_id: int | None = None, username: str | None = None):
        """Every Test for `project_id` with that exact
        session_id — None (the default) means every whole-project-scope
        run, not "no filter". `username`, when given, further narrows to
        that user's runs; omitted, no username filter is applied. Most
        recent first."""
        if username is None:
            return self.testing_service.list_runs(project_id, session_id)
        return self.testing_service.list_runs(project_id, session_id, username)

    @post("/api/skills/testing/projects/{project_id}/runs/states/{state_key}", role="supervisor")
    def post_state_test(self, project_id: str, state_key: str, req: StateTestRequest):
        try:
            self.testing_service.start_job(project_id, state_key, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    @post("/api/skills/testing/projects/{project_id}/runs/signals/{signal_name}", role="supervisor")
    def post_signal_test(self, project_id: str, signal_name: str, req: StateTestRequest):
        try:
            self.testing_service.start_signal_job(project_id, signal_name, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    @post("/api/skills/testing/projects/{project_id}/aggregations/states", role="supervisor")
    def post_states_aggregation(self, project_id: str, req: StateTestRequest):
        try:
            self.testing_service.start_all_states_job(project_id, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    @post("/api/skills/testing/projects/{project_id}/aggregations/signals", role="supervisor")
    def post_signals_aggregation(self, project_id: str, req: StateTestRequest):
        try:
            self.testing_service.start_all_signals_job(project_id, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    @post("/api/skills/testing/projects/{project_id}/aggregations/root", role="supervisor")
    def post_root_aggregation(self, project_id: str, req: StateTestRequest):
        try:
            self.testing_service.start_root_job(project_id, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    @post("/api/skills/testing/projects/{project_id}/aggregations/users", role="supervisor")
    def post_users_aggregation(self, project_id: str, req: StateTestRequest):
        try:
            self.testing_service.start_users_aggregation_job(project_id, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    @post("/api/skills/testing/projects/{project_id}/runs/sessions", role="supervisor")
    def post_sessions_run(self, project_id: str, req: StateTestRequest):
        try:
            self.testing_service.start_sessions_run_job(project_id, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    @post("/api/skills/testing/projects/{project_id}/runs/users/{username}", role="supervisor")
    def post_user_sessions_run(self, project_id: str, username: str, req: StateTestRequest):
        try:
            self.testing_service.start_user_sessions_run_job(username, project_id, req.strategy)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return {"success": True}

    @get("/api/skills/testing/projects/{project_id}/aggregations/result", role="supervisor")
    def get_aggregate_result(self, project_id: str, kind: str, strategy: str, target: str | None = None):
        result = self.testing_service.get_aggregate_result(project_id, kind, target, strategy)
        if result is None:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail="No aggregate result for this key yet.")
        return result

    @get("/api/skills/testing/projects/{project_id}/status", role="supervisor")
    def get_test_status(self, project_id: str):
        return {
            "events": self.progress_broadcaster.snapshot(),
            "tokens": self.progress_broadcaster.total_tokens(),
        }
