from __future__ import annotations

from auth.auth_service import AuthService
from schemas import SetUserRoleRequest

from .base_controller import BaseController, get, put


class UserController(BaseController):

    def __init__(self, auth_service: AuthService) -> None:
        self.auth_service = auth_service

    @get("/api/users", role="supervisor")
    def get_users(self):
        return {"users": self.auth_service.list_users()}

    @put("/api/users/{user_id}/role", role="admin")
    def put_user_role(self, user_id: str, req: SetUserRoleRequest):
        return self.auth_service.set_user_role(user_id, req.role)
