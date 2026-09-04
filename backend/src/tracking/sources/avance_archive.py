"""The `avance` source driver — read-only access to one of a project's
own stored archive files, addressed by a `sources:` entry's own
`url: avance:<archive path>` (e.g. `avance:behaviour/flights.csv`).
`read()` is source.attachment(name)'s exact successor — same Db calls,
same content-type guard — resolved once from the declared url instead of
a per-call argument. `select(value)` replaces search(): the same
per-line case-insensitive match against that one file."""
from __future__ import annotations

from automaton.automaton import Automaton
from db import Db

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

    def __init__(self, db: Db, automaton: Automaton, name: str, archive_path: str) -> None:
        super().__init__(name)
        self._db = db
        self._automaton = automaton
        self._archive_path = archive_path

    def _read_text(self) -> str:
        if self._automaton.revision is None:
            raise ValueError(f"source.{self._name}: this automaton has no known storage location to read from.")
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
        return content.decode("utf-8")

    def read(self) -> str:
        return self._read_text()

    def select(self, value: str) -> str:
        lines = self._read_text().splitlines(keepends=True)
        if not lines:
            return ""
        needle = value.lower()
        matches = [line for line in lines[1:] if needle in line.lower()]
        return lines[0] + "".join(matches)
