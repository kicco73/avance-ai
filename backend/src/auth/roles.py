from __future__ import annotations

ROLE_LEVELS = {"pending": -1, "user": 0, "customer": 1, "supervisor": 2, "admin": 3}


def role_satisfies(user_role: str, required_role: str) -> bool:
    return ROLE_LEVELS.get(user_role, -1) >= ROLE_LEVELS.get(required_role, 0)
