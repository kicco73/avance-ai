"""Provider-agnostic authentication interface — one verify() call turning
a client-supplied credential into a confirmed identity. Each concrete
provider (see providers/) owns how its own credential is actually
verified; AuthService never knows the details.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class AuthenticatedUser:
    # The provider's own opaque, stable id for this account (Google: the
    # "sub" claim) — never the free-string username the rest of the
    # codebase already uses (see db/users.py's own note on that).
    provider_user_id: str
    email: str
    name: str
    picture_url: str | None
    role: str = "user"


class AuthProvider(ABC):
    @abstractmethod
    def verify(self, credential: str) -> AuthenticatedUser:
        """Raises AuthError (see auth/errors.py) if `credential` doesn't
        verify — expired, tampered, or issued for a different audience."""
        raise NotImplementedError

    @abstractmethod
    def public_config(self) -> dict:
        raise NotImplementedError
