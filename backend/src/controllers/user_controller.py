from __future__ import annotations

from auth.auth_service import AuthService

from .base_controller import BaseController, get


class UserController(BaseController):

    def __init__(self, auth_service: AuthService) -> None:
        self.auth_service = auth_service

    @get("/api/users", role="admin")
    def get_users(self):
        return {"users": self.auth_service.list_users()}
