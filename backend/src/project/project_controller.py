"""Everything a caller asks *about a project*: its identifiers and
metrics, one user's signals and timeline, the sessions on it, and the
legal terms a person has to accept before a turn can run.

Core, not platform. These are questions about a project rather than about
the authoring environment, and the terms in particular gate every
channel — WhatsApp reads them through the turn service, and a product
with no editor still has to be able to answer them.
"""
from __future__ import annotations

from http import HTTPStatus

from fastapi import HTTPException

from controllers.base_controller import BaseController, get, post
from project.project_service import ProjectService
from turn.turn_service import TurnService


class ProjectController(BaseController):

    def __init__(self, turn_service: TurnService, project_service: ProjectService) -> None:
        self.turn_service = turn_service
        self.project_service = project_service

    @get("/api/projects/{project_id}/identifiers")
    def get_identifiers(self, project_id: str):
        """`project_id`'s own identifier registry — every identifier a
        trigger/`env:` expression can reference, one {identifier:
        description} dict per namespace."""
        self.project_service.ensure_project_not_broken(project_id)
        try:
            return self.project_service.get_identifier_registry(project_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc

    @get("/api/projects/{project_id}/metrics")
    def get_metrics(self, project_id: str, message_id: int | None = None, full: bool = False, username: str | None = None):
        """Core metrics for `project_id`, live or (`message_id` given)
        as of that exact message — no caching. `full`: every core metric,
        including ones that need more than one session (e.g. Retention),
        instead of the usual "one_session" subset. `username` (omitted:
        the caller's own sessions): Manage Users' statistics panel, to
        inspect a specific user's sessions rather than its own. TurnServiceError
        for an unknown message_id is handled globally, see error_handlers.py."""
        return self.turn_service.get_metrics(
            project_id=project_id, message_id=message_id, full=full, username=username,
        )

    @get("/api/projects/{project_id}/users/{username}/latest-signals")
    def get_user_latest_signals(self, project_id: str, username: str):
        """The most recent live session's own latest signal snapshot for
        `username` in `project_id` — Manage Users' Signals tab."""
        return self.turn_service.get_latest_signal_values(project_id, username)

    @get("/api/projects/{project_id}/users/{username}/timeline")
    def get_user_timeline(self, project_id: str, username: str):
        return self.turn_service.get_timeline(project_id, username)

    @get("/api/projects/{project_id}/users/{username}/metrics-history")
    def get_user_metrics_history(self, project_id: str, username: str):
        return self.turn_service.get_metrics_history(project_id, username)

    @get("/api/projects/{project_id}/legal-terms-status")
    def get_legal_terms_status(self, project_id: str):
        return self.turn_service.get_legal_terms_status(project_id)

    @post("/api/projects/{project_id}/accept-terms")
    def post_accept_chat_terms(self, project_id: str):
        self.turn_service.accept_legal_terms(project_id)
        return {"success": True}

    @get("/api/projects/{project_id}/sessions")
    def get_sessions(self, project_id: str, include_imported: bool = False):
        """Every session for `project_id`, for the "Sessions" side
        panel — see TurnService.list_sessions."""
        return self.turn_service.list_sessions(include_imported=include_imported, project_id=project_id)
