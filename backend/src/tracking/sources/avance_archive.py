"""The `avance` source driver — read-only access to one of a project's
own stored archive files, addressed by a `sources:` entry's own
`url: avance:<archive path>` (e.g. `avance:sources/flights.csv`).
`read()` is source.attachment(name)'s exact successor — same content-type
guard — resolved once from the declared url instead of a per-call
argument. `select(value)` replaces search(): the same per-line
case-insensitive match against that one file.

Every read goes through a per-chat-session cache copy under
`{CACHE_DIR}/sessions/<session id>/<archive path>` rather than the
canonical archive directly — see _read_text's own docstring for why."""
from __future__ import annotations

from automaton.automaton import Automaton
from db import Db
from project.archive.layout import CACHE_DIR

from .base import SourceDriver

SCHEME = "avance"


class AvanceArchiveSource(SourceDriver):
    SUPPORTED_METHODS = frozenset({"read", "select"})
    METHOD_DESCRIPTIONS = {
        "read": "This source's own archive file, read as plain text — e.g. source.<name>.read().",
        "select": (
            "Grep over this source's own archive file: the header row plus every row containing "
            "`value` (case-insensitive) — e.g. source.<name>.select('Paris')."
        ),
    }

    def __init__(self, db: Db, automaton: Automaton, name: str, archive_path: str, session_id: int | None = None) -> None:
        super().__init__(name)
        self._db = db
        self._automaton = automaton
        self._archive_path = archive_path
        # None outside a real chat session (a wake-up re-evaluation, a
        # test replay, an on-enter deferred call with no session recorded)
        # — read() falls back to the canonical archive directly then,
        # since there's no session of its own for a cache copy to belong to.
        self._session_id = session_id

    def _cache_archive_path(self) -> str:
        return f"{CACHE_DIR}/sessions/{self._session_id}/{self._archive_path}"

    def _read_canonical(self) -> tuple[str, str]:
        """(content, content_type) straight off the canonical archive, at
        this automaton's own pinned revision — never Db's own "current"
        default, wrong for a session pinned to an older one."""
        content_type = self._db.get_archive_content_type(
            self._automaton.project_id, self._archive_path, revision=self._automaton.revision,
        )
        if content_type is None:
            raise ValueError(
                f"source.{self._name}: '{self._archive_path}' not found in project '{self._automaton.project_id}'."
            )
        if not content_type.startswith("text/"):
            raise ValueError(
                f"source.{self._name}: '{self._archive_path}' is a binary file ({content_type}) — "
                "only text files can be read this way."
            )
        content = self._db.get_archive(self._automaton.project_id, self._archive_path, revision=self._automaton.revision)
        assert content is not None  # same Archive row get_archive_content_type just found this content_type on
        return content.decode("utf-8"), content_type

    def _read_text(self) -> str:
        """Reads through this session's own cache copy of the archive
        (see _cache_archive_path), duplicating it from the canonical one
        on a miss — first read of a session (only) pays for a second Db
        round trip; a session-scoped copy also means the rest of a
        conversation keeps seeing the same content even if the project
        gets edited/republished underneath it mid-session."""
        if self._automaton.revision is None:
            raise ValueError(f"source.{self._name}: this automaton has no known storage location to read from.")
        # project_id is always set by the time revision is (see
        # Automaton.set_storage_location's own docstring) — revision alone
        # is the "no known storage location" signal checked above.
        assert self._automaton.project_id is not None
        if self._session_id is None:
            content, _ = self._read_canonical()
            return content
        cache_path = self._cache_archive_path()
        cached = self._db.get_archive(self._automaton.project_id, cache_path, revision=self._automaton.revision)
        if cached is not None:
            return cached.decode("utf-8")
        content, content_type = self._read_canonical()
        self._db.write_archive_at_revision(
            self._automaton.project_id, cache_path, self._automaton.revision, content.encode("utf-8"), content_type,
        )
        return content

    def read(self) -> str:
        return self._read_text()

    def select(self, value: str) -> str:
        lines = self._read_text().splitlines(keepends=True)
        if not lines:
            return ""
        needle = value.lower()
        matches = [line for line in lines[1:] if needle in line.lower()]
        return lines[0] + "".join(matches)
