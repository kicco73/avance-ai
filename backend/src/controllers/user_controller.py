from __future__ import annotations

from db import Db

from .base_controller import BaseController, get


class UserController(BaseController):

    def __init__(self, db: Db) -> None:
        self.db = db

    @get("/api/users")
    def get_users(self):
        return {"users": self.db.list_users()}
