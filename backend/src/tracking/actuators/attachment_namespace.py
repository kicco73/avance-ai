"""The on-enter-only `attachment` namespace — `attachment.read(name)`
returns one of this project's own archive files' whole text content,
resolved the same "exact path or unique basename under `behaviour/`" way
a source's own `url:` is (see AutomatonBuilder._extract_required_archives).
Every call is validated at build time only (see AutomatonBuilder.
_validate_attachment_read): `name` must be a string literal naming a text
file no bigger than MAX_ATTACHMENT_READ_BYTES. A published revision is
immutable, so nothing here re-checks size at runtime the way source.
select's own MAX_SOURCE_RESULT_CHARS bound does — existence/text-type are
still checked defensively, the same way AvanceArchiveSource's own
_read_canonical does."""
from __future__ import annotations

from pathlib import Path

from automaton.automaton import Automaton
from db import Db

MAX_ATTACHMENT_READ_BYTES = 64 * 1024


class AttachmentNamespace:
    def __init__(self, db: Db, automaton: Automaton) -> None:
        self._db = db
        self._automaton = automaton

    def read(self, name: str) -> str:
        assert self._automaton.project_id is not None
        assert self._automaton.revision is not None
        archives = self._db.get_archives(self._automaton.project_id, revision=self._automaton.revision)
        resolved = self._resolve(name, archives)
        if resolved is None:
            raise ValueError(f"attachment.read('{name}'): not found in project '{self._automaton.project_id}'.")
        content_type = self._db.get_archive_content_type(
            self._automaton.project_id, resolved, revision=self._automaton.revision,
        )
        if content_type is None or not content_type.startswith("text/"):
            raise ValueError(f"attachment.read('{name}'): '{resolved}' is a binary file — only text files can be read this way.")
        return archives[resolved].decode("utf-8")

    @staticmethod
    def _resolve(name: str, archives: dict[str, bytes]) -> str | None:
        if name in archives:
            return name
        matches = [archive_name for archive_name in archives if Path(archive_name).name == name]
        return matches[0] if len(matches) == 1 else None
