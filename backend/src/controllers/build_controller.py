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

from controllers.base_controller import BaseController, post


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
