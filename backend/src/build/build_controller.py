"""The Build view's own routes.

Two builds and the list of what either may leave out. "Local module"
compiles the project into a package and returns when it is done; "backend
copy" builds a whole backend around it and streams its steps back, since
it ends by running that backend's own tests (see build/build_job.py).
The Target step's other options — zip, push to a repository — have no
backend behind them yet, and this controller does not pretend otherwise.
"""
from __future__ import annotations

from http import HTTPStatus

from build import BuildService, CompileError
from fastapi import HTTPException

from scheduler import SchedulerService
from controllers.base_controller import BaseController, get, post
from pydantic import BaseModel


class BuildBackendCopyRequest(BaseModel):
    excluded_skills: list[str] = []


class BuildController(BaseController):

    def __init__(self, build_service: BuildService, scheduler_service: SchedulerService) -> None:
        self.build_service = build_service
        self.scheduler_service = scheduler_service

    @post("/api/skills/build/projects/{project_id}/local-module", role="admin")
    def post_build_local_module(self, project_id: str):
        """Compiles `project_id`'s published revision into build/<name>/.
        A CompileError is the project's own problem, not a server fault,
        so it comes back as a 400 the panel shows verbatim."""
        try:
            return self.build_service.build_local_module(project_id)
        except CompileError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/skills/build/projects/{project_id}/requirements", role="admin")
    def get_project_build_skills(self, project_id: str):
        """The installed roster (GET /api/skills) plus what this project's
        own automaton makes mandatory (see BuildService.installed_skills)."""
        try:
            return self.build_service.installed_skills(project_id)
        except CompileError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @post("/api/skills/build/projects/{project_id}/backend-copy", role="admin")
    async def post_build_backend_copy(self, project_id: str, req: BuildBackendCopyRequest | None = None):
        """`excluded_skills` names the packages this build leaves out.
        Absent means a full build: a client that does not know about a
        skill can never drop one by accident.

        Answers with the build's progress as it happens, not with its
        result: a backend copy ends by running the built backend's own
        test suite, which is minutes, so this streams the job's steps on
        the same response and the last chunk carries the report (see
        SchedulerService.stream_progress). What cannot be built at all —
        a project with unpublished changes — is still a 400 here, before
        any job exists.

        `async` deliberately: the progress connection is an asyncio queue
        bound to the loop it is created on, and a sync handler runs in a
        threadpool with no loop at all — same reason the upload and import
        routes are async."""
        try:
            job = self.build_service.backend_copy_job(project_id, req.excluded_skills if req else None)
        except CompileError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        return self.scheduler_service.stream_progress(job)
