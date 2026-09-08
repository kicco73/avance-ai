"""The task-only `attachment` namespace — `attachment.read(name)`
returns one of this project's own archive files' whole text content,
resolved the same "exact path or unique basename under `behaviour/`" way
a source's own `url:` is (see AutomatonBuilder._extract_required_archives).
Every call is validated at build time only (see AutomatonBuilder.
_validate_attachment_read): `name` must be a string literal naming a text
file no bigger than MAX_ATTACHMENT_READ_BYTES. A published revision is
immutable, so nothing here re-checks size at runtime the way source.
select's own MAX_SOURCE_RESULT_CHARS bound does — existence/text-type are
still checked defensively, the same way a source's own read does.

Where the file itself comes from is not this namespace's business: it
asks the ProjectFiles it was handed (see tracking.project_files), so an
automaton with no storage location — a compiled one — reads the very same
attachment out of what it already carries."""
from __future__ import annotations

from automaton.automaton import Automaton
from tracking.project_files import ProjectFiles

MAX_ATTACHMENT_READ_BYTES = 64 * 1024


class AttachmentNamespace:
    def __init__(self, files: ProjectFiles, automaton: Automaton) -> None:
        self._files = files
        self._automaton = automaton

    def read(self, name: str) -> str:
        resolved = self._files.resolve(name)
        if resolved is None:
            raise ValueError(f"attachment.read('{name}'): not found in project '{self._automaton.project_id}'.")
        found = self._files.read(resolved)
        if found is None or not found[1].startswith("text/"):
            raise ValueError(f"attachment.read('{name}'): '{resolved}' is a binary file — only text files can be read this way.")
        return found[0].decode("utf-8")

