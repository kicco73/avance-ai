"""The Build view's own routes.

One route today, and deliberately only one: the Target step's "Local
module" build, which compiles the project into a package under src/build/
(see build.build_service.BuildService). The step's other targets — zip,
push to a repository — have no backend behind them yet, and this
controller does not pretend otherwise.
"""
from __future__ import annotations

from http import HTTPStatus

from build import BuildService, CompileError
from fastapi import HTTPException

from system import skills
from controllers.base_controller import BaseController, get, post
from pydantic import BaseModel


class BuildBackendCopyRequest(BaseModel):
    excluded_skills: list[str] = []


class BuildController(BaseController):

    def __init__(self, build_service: BuildService) -> None:
        self.build_service = build_service

    @post("/api/projects/{project_id}/build/local-module", role="admin")
    def post_build_local_module(self, project_id: str):
        """Compiles `project_id`'s published revision into build/<name>/.
        A CompileError is the project's own problem, not a server fault,
        so it comes back as a 400 the panel shows verbatim."""
        try:
            return self.build_service.build_local_module(project_id)
        except CompileError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/build/skills", role="admin")
    def get_build_skills(self):
        """What this backend has installed and a build may leave out —
        read off the source tree, never a list someone maintains (see
        skills.installed)."""
        return {"skills": skills.installed()}

    @post("/api/projects/{project_id}/build/backend-copy", role="admin")
    def post_build_backend_copy(self, project_id: str, req: BuildBackendCopyRequest | None = None):
        """`excluded_skills` names the packages this build leaves out.
        Absent means a full build: a client that does not know about a
        skill can never drop one by accident."""
        try:
            return self.build_service.build_backend_copy(project_id, req.excluded_skills if req else None)
        except CompileError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
