from __future__ import annotations

from datetime import datetime

from .models import UserProject


class UserProjectMixin:

    def get_accepted_terms_archive_id(self, username: str, project_id: str) -> int | None:
        row = UserProject.get_or_none(
            (UserProject.user == username) & (UserProject.project == project_id)
        )
        return row.accepted_terms_id if row is not None else None

    def user_has_project_access(self, username: str, project_id: str) -> bool:
        return UserProject.get_or_none(
            (UserProject.user == username) & (UserProject.project == project_id)
        ) is not None

    def install_project(self, username: str, project_id: str) -> None:
        UserProject.get_or_create(user=username, project=project_id)

    def uninstall_project(self, username: str, project_id: str) -> None:
        UserProject.delete().where(
            (UserProject.user == username) & (UserProject.project == project_id)
        ).execute()

    def record_terms_acceptance(self, username: str, project_id: str, archive_id: int) -> None:
        row, created = UserProject.get_or_create(
            user=username, project=project_id, defaults={"accepted_terms": archive_id},
        )
        if not created and row.accepted_terms_id != archive_id:
            row.accepted_terms = archive_id
            row.save()

    def record_invite_redemption(self, username: str, project_id: str, invite_id: int, timestamp: datetime) -> None:
        """The other half of an invite-based registration (see
        InviteManager.redeem) — same get_or_create shape as
        record_terms_acceptance above, just independent fields: a brand
        new registration's own UserProject row never exists yet, but this
        stays idempotent/safe regardless, same reasoning as that one."""
        row, created = UserProject.get_or_create(
            user=username, project=project_id, defaults={"invite": invite_id, "invite_timestamp": timestamp},
        )
        if not created:
            row.invite = invite_id
            row.invite_timestamp = timestamp
            row.save()
