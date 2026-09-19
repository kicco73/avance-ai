from __future__ import annotations

from typing import TYPE_CHECKING

from project.archive.layout import CACHE_DIR

from .avance_archive import AvanceArchiveSource
from .base import SourceContext

if TYPE_CHECKING:
    from automaton.automaton import Automaton
    from db import Db

SCHEME = "websearch"
USER_SCOPE = "user"
CONTENT_TYPE = "text/csv"


def archive_name(session_id: int) -> str:
    """Under `cache/sessions/<session_id>/`, the same prefix
    close_session/reset_test_sessions/delete_session already wipe (see
    turn.sessions.session_manager, turn.turn_service) — a websearch
    result lives no longer than the session that found it."""
    return f"{CACHE_DIR}/sessions/{session_id}/{SCHEME}"


class WebSearchArchive:

    def __init__(self, db: "Db", project_id: str, revision: int, session_id: int) -> None:
        self._db = db
        self._project_id = project_id
        self._revision = revision
        self._session_id = session_id

    @property
    def name(self) -> str:
        return archive_name(self._session_id)

    def write(self, csv_text: str) -> None:
        self._db.write_archive_at_revision(
            self._project_id, self.name, self._revision, csv_text.encode("utf-8"), CONTENT_TYPE,
        )

    def clear(self) -> None:
        self.write("")

    def read(self) -> str:
        """An entry nobody has written yet reads as an empty cache file,
        never as a missing one: this session simply has no results, which
        is the same table `task.websearch(...)` leaves behind for a
        search that matched nothing."""
        content = self._db.get_archive(self._project_id, self.name, revision=self._revision)
        return content.decode("utf-8") if content is not None else ""


class NoWebSearchArchive(WebSearchArchive):

    def __init__(self) -> None:
        pass

    @property
    def name(self) -> str:
        return ""

    def write(self, csv_text: str) -> None:
        return None

    def clear(self) -> None:
        return None

    def read(self) -> str:
        return ""


def websearch_archive_for(db: "Db | None", automaton: "Automaton", session_id: int | None) -> WebSearchArchive:
    if db is None or automaton.project_id is None or automaton.revision is None or session_id is None:
        return NoWebSearchArchive()
    return WebSearchArchive(db, automaton.project_id, automaton.revision, session_id)


class WebSearchSource(AvanceArchiveSource):

    def __init__(self, context: SourceContext, name: str, path: str) -> None:
        super().__init__(context, name, path)
        self._archive = websearch_archive_for(context.db, context.automaton, context.session_id)

    def _read_text(self) -> str:
        return self._archive.read()
