from __future__ import annotations

from datetime import datetime

from .models import Invite, UserProject


class InviteMixin:

    def create_invite(
        self, code: str, project_name: str, created_by: str | None, expires_at: datetime, max_shares: int,
    ) -> Invite:
        return Invite.create(
            code=code, project_name=project_name, created_by=created_by,
            expires_at=expires_at, max_shares=max_shares,
        )

    def get_invite_by_code(self, code: str) -> Invite | None:
        return Invite.get_or_none(Invite.code == code)

    def count_invite_redemptions(self, invite_id: int) -> int:
        """How many UserProject rows this invite actually brought in —
        never a counter stored on Invite itself (see models.py's own
        docstring on Invite/UserProject.invite)."""
        return UserProject.select().where(UserProject.invite == invite_id).count()
