from __future__ import annotations

from typing import TYPE_CHECKING

from project.archive.layout import CACHE_DIR
from system.web_session import WebSession

from .avance_archive import AvanceArchiveSource
from .base import SourceContext

if TYPE_CHECKING:
    from automaton.automaton import Automaton
    from db import Db

SCHEME = "websearch"
USER_SCOPE = "user"
ARCHIVE_ROOT = f"{CACHE_DIR}/{SCHEME}"
CONTENT_TYPE = "text/csv"


def archive_name(project_id: str, user_id: str) -> str:
    return f"{ARCHIVE_ROOT}/{project_id}/{user_id}"


class WebSearchArchive:

    def __init__(self, db: "Db", project_id: str, revision: int) -> None:
        self._db = db
        self._project_id = project_id
        self._revision = revision

    @property
    def name(self) -> str:
        return archive_name(self._project_id, WebSession().user)

    def write(self, csv_text: str) -> None:
        self._db.write_archive_at_revision(
            self._project_id, self.name, self._revision, csv_text.encode("utf-8"), CONTENT_TYPE,
        )

    def clear(self) -> None:
        self.write("")

    def read(self) -> str:
        """An entry nobody has written yet reads as an empty cache file,
        never as a missing one: this user simply has no results, which is
        the same table `task.websearch(...)` leaves behind for a search
        that matched nothing."""
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


def websearch_archive_for(db: "Db | None", automaton: "Automaton") -> WebSearchArchive:
    if db is None or automaton.project_id is None or automaton.revision is None:
        return NoWebSearchArchive()
    return WebSearchArchive(db, automaton.project_id, automaton.revision)


class WebSearchSource(AvanceArchiveSource):

    def __init__(self, context: SourceContext, name: str, path: str) -> None:
        super().__init__(context, name, path)
        self._archive = websearch_archive_for(context.db, context.automaton)

    def _read_text(self) -> str:
        return self._archive.read()
