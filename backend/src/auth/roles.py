from __future__ import annotations

ROLE_LEVELS = {"user": 0, "supervisor": 1, "admin": 2}


def role_satisfies(user_role: str, required_role: str) -> bool:
    return ROLE_LEVELS.get(user_role, -1) >= ROLE_LEVELS.get(required_role, 0)
