from __future__ import annotations

from .models import UserProject


class UserProjectMixin:

    def get_accepted_terms_archive_id(self, username: str, project_name: str) -> int | None:
        row = UserProject.get_or_none(
            (UserProject.user == username) & (UserProject.project_name == project_name)
        )
        return row.accepted_terms_id if row is not None else None

    def record_terms_acceptance(self, username: str, project_name: str, archive_id: int) -> None:
        row, created = UserProject.get_or_create(
            user=username, project_name=project_name, defaults={"accepted_terms": archive_id},
        )
        if not created and row.accepted_terms_id != archive_id:
            row.accepted_terms = archive_id
            row.save()
