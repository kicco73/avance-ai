from __future__ import annotations

from .models import AppRating


class RatingMixin:

    def get_app_rating(self, user_id: str, project_id: str, revision: int) -> int | None:
        row = AppRating.get_or_none(
            (AppRating.user == user_id) & (AppRating.project == project_id) & (AppRating.revision == revision)
        )
        return row.rating if row is not None else None

    def set_app_rating(self, user_id: str, project_id: str, revision: int, rating: int) -> None:
        row, created = AppRating.get_or_create(
            user=user_id, project=project_id, revision=revision, defaults={"rating": rating},
        )
        if not created and row.rating != rating:
            row.rating = rating
            row.save()
