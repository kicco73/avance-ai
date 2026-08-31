from __future__ import annotations

from datetime import datetime

from .models import UserProject


class UserProjectMixin:

    def get_accepted_terms_archive_id(self, username: str, project_name: str) -> int | None:
        row = UserProject.get_or_none(
            (UserProject.user == username) & (UserProject.project_name == project_name)
        )
        return row.accepted_terms_id if row is not None else None

    def user_has_project_access(self, username: str, project_name: str) -> bool:
        return UserProject.get_or_none(
            (UserProject.user == username) & (UserProject.project_name == project_name)
        ) is not None

    def record_terms_acceptance(self, username: str, project_name: str, archive_id: int) -> None:
        row, created = UserProject.get_or_create(
            user=username, project_name=project_name, defaults={"accepted_terms": archive_id},
        )
        if not created and row.accepted_terms_id != archive_id:
            row.accepted_terms = archive_id
            row.save()

    def record_invite_redemption(self, username: str, project_name: str, invite_id: int, timestamp: datetime) -> None:
        """The other half of an invite-based registration (see
        InviteManager.redeem) — same get_or_create shape as
        record_terms_acceptance above, just independent fields: a brand
        new registration's own UserProject row never exists yet, but this
        stays idempotent/safe regardless, same reasoning as that one."""
        row, created = UserProject.get_or_create(
            user=username, project_name=project_name, defaults={"invite": invite_id, "invite_timestamp": timestamp},
        )
        if not created:
            row.invite = invite_id
            row.invite_timestamp = timestamp
            row.save()
