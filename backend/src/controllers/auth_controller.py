"""LoginView.vue's own backend surface — login/logout and the current
user, per the auth-service section-0 config and the auth middleware
(main.py) that gates every other route.
"""
from __future__ import annotations

from http import HTTPStatus

from fastapi import HTTPException, Response

from auth.auth_service import SESSION_COOKIE_NAME, AuthService
from schemas import LoginRequest
from session import Session

from .base_controller import BaseController, get, post


class AuthController(BaseController):

    def __init__(self, auth_service: AuthService) -> None:
        self.auth_service = auth_service

    @get("/api/auth/providers")
    def get_providers(self):
        return {"providers": self.auth_service.public_providers()}

    @post("/api/auth/login")
    def post_login(self, req: LoginRequest, response: Response):
        try:
            token = self.auth_service.login(req.provider, req.credential)
        except ValueError as exc:
            raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=str(exc)) from exc
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=int(self.auth_service.token_ttl.total_seconds()),
        )
        return {"success": True}

    @post("/api/auth/logout")
    def post_logout(self, response: Response):
        response.delete_cookie(key=SESSION_COOKIE_NAME)
        return {"success": True}

    @get("/api/auth/me")
    def get_me(self):
        """The auth middleware already validated the cookie for this
        request to have reached here at all — this just reflects the
        username it resolved into Session().user."""
        return {"user": Session().user}
