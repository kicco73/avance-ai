from __future__ import annotations

from .instrumentation import instrument_queries, write
from .models import AppRating


@instrument_queries
class RatingMixin:

    def get_app_rating(self, user_id: str, project_id: str, revision: int) -> int | None:
        row = AppRating.get_or_none(
            (AppRating.user == user_id) & (AppRating.project == project_id) & (AppRating.revision == revision)
        )
        return row.rating if row is not None else None

    @write
    def set_app_rating(self, user_id: str, project_id: str, revision: int, rating: int, session_id: int | None = None) -> None:
        row, created = AppRating.get_or_create(
            user=user_id, project=project_id, revision=revision, defaults={"rating": rating, "session": session_id},
        )
        if not created and (row.rating != rating or (row.session_id is None and session_id is not None)):
            row.rating = rating
            if row.session_id is None:
                row.session = session_id
            row.save()
