"""The login wall itself: an ASGI (BaseHTTPMiddleware) layer in front of
every HTTP route except the small allowlist below — no per-route
Depends(), so every existing controller method stays untouched.

Only covers HTTP: BaseHTTPMiddleware never sees a websocket handshake at
all (Starlette dispatches those on a separate ASGI path). /ws/chat's own
equivalent check lives in chat/ws_adapter.py's WsAdapter.chat_loop,
before websocket.accept() — same AuthService.verify_token() call, same
cookie, just triggered from a different entrypoint.

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

from auth.auth_service import SESSION_COOKIE_NAME
from session import Session

# Exact paths only — never a prefix match, so a future route nested under
# /api/auth/ doesn't silently bypass the wall by sharing that prefix.
# The three doc routes are FastAPI's own defaults (main.py never disables
# them); there's no separate /health endpoint today.
ALLOWED_PATHS = frozenset({"/api/auth/login", "/docs", "/redoc", "/openapi.json"})


def _unauthenticated_response() -> JSONResponse:
    return JSONResponse(
        status_code=HTTPStatus.UNAUTHORIZED,
        content={"error": {"message": "Not authenticated.", "detail": None}},
    )


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in ALLOWED_PATHS:
            return await call_next(request)

        auth_service = request.app.state.auth_service
        token = request.cookies.get(SESSION_COOKIE_NAME)
        identity = auth_service.verify_token(token) if token else None
        if identity is None:
            return _unauthenticated_response()

        Session().user = identity.email
        return await call_next(request)
