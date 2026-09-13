"""The login wall itself: an ASGI (BaseHTTPMiddleware) layer in front of
every HTTP route except the small allowlist below — no per-route
Depends(), so every existing controller method stays untouched.

Only covers HTTP, and still does now that the bus channel sits under
/api/ like every other route: BaseHTTPMiddleware returns early for any
scope that is not "http", so it never sees a websocket handshake at all.
/api/core/bus's own equivalent check lives in system/bus_channel.py's
BusChannel.channel_loop, before websocket.accept() — same
AuthService.verify_token() call, same cookie, just triggered from a
different entrypoint.

Reads the AuthService off `request.app.state.auth_service` rather than a
constructor argument: add_middleware() runs synchronously in create_app(),
before AuthService exists (it's built inside main.py's own lifespan,
alongside `db`) — app.state is the standard way to bridge a lifespan-
scoped dependency into a middleware that was registered before it existed.
By the time any real request's dispatch() runs, startup has long finished
and app.state.auth_service is set.
"""
from __future__ import annotations

from http import HTTPStatus

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Match

from auth.auth_service import SESSION_COOKIE_NAME
from auth.roles import role_satisfies
from system.web_session import WebSession
ALLOWED_PATHS = frozenset({"/docs", "/redoc", "/openapi.json"})


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in ALLOWED_PATHS:
            return await call_next(request)

        matched_route, path_params = self._matched_route_for(request)
        required_role = getattr(matched_route.endpoint, "__required_role__", "user") if matched_route else "user"
        if required_role is None:
            return await call_next(request)

        auth_service = request.app.state.auth_service
        token = request.cookies.get(SESSION_COOKIE_NAME)
        identity = auth_service.verify_token(token) if token else None
        if identity is None:
            return self._unauthenticated_response()

        if not role_satisfies(identity.role, required_role):
            return self._forbidden_response()
        project_id = path_params.get("project_id")
        if not role_satisfies(identity.role, 'supervisor') and project_id is not None:
            db = request.app.state.db
            if not db.user_has_project_access(identity.email, project_id):
                return self._forbidden_response()

        WebSession().user = identity.email
        WebSession().role = identity.role
        return await call_next(request)

    @staticmethod
    def _unauthenticated_response() -> JSONResponse:
        return JSONResponse(
            status_code=HTTPStatus.UNAUTHORIZED,
            content={"error": {"message": "Not authenticated.", "detail": None}},
        )

    @staticmethod
    def _forbidden_response() -> JSONResponse:
        return JSONResponse(
            status_code=HTTPStatus.FORBIDDEN,
            content={"error": {"message": "Not authorized for this action.", "detail": None}},
        )

    @classmethod
    def _flatten_routes(cls, routes):
        for candidate_route in routes:
            original_router = getattr(candidate_route, "original_router", None)
            if original_router is not None:
                yield from cls._flatten_routes(original_router.routes)
            else:
                yield candidate_route

    @classmethod
    def _matched_route_for(cls, request: Request):
        for candidate_route in cls._flatten_routes(request.app.routes):
            match, child_scope = candidate_route.matches(request.scope)
            if match == Match.FULL:
                return candidate_route, child_scope.get("path_params", {})
        return None, {}
