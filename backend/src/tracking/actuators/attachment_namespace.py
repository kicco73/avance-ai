"""The `attachment` namespace — `attachment.<doc_id>.read()` returns
one of this project's own files under its `behaviour/` folder, whole, as
text, and `attachment.<doc_id>.render()` the same text with every
`{{ expression }}` in it replaced by that expression's value. `doc_id` is
derived from the file's name exactly as `media.<doc_id>` is (see
automaton.file_types.doc_id_for). Every reference is validated at build
time (see AutomatonValidator.validate_attachment_files): the file must
exist, be text, be no bigger than MAX_ATTACHMENT_READ_BYTES, and every
expression a rendered one holds must be one the script could write.

Where the file itself comes from is not this namespace's business: it
asks the ProjectFiles it was handed (see tracking.project_files), so an
automaton with no storage location — a compiled one — reads the very same
attachment out of what it already carries."""
from __future__ import annotations

from typing import Any

from automaton.automaton import Automaton
from automaton.core import AttachmentTemplate, ScopedNamespace
from automaton.file_types import attachment_doc_id_for
from automaton.scope import EvaluationScope
from tracking.project_files import ProjectFiles

MAX_ATTACHMENT_READ_BYTES = 64 * 1024


class AttachmentDoc:
    def __init__(self, files: ProjectFiles, path: str, names: EvaluationScope | None) -> None:
        self._files = files
        self._path = path
        self._names = names

    def read(self) -> str:
        found = self._files.read(self._path)
        if found is None or not found[1].startswith("text/"):
            raise ValueError(f"attachment: '{self._path}' is a binary file — only text files can be read this way.")
        return found[0].decode("utf-8")

    def render(self) -> str:
        return AttachmentTemplate(self.read()).rendered(self._names)


class AttachmentNamespace(ScopedNamespace):
    def __init__(self, files: ProjectFiles, automaton: Automaton) -> None:
        self._files = files
        self._automaton = automaton

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        paths = [path for path in self._files.names() if attachment_doc_id_for(path) == name]
        if len(paths) != 1:
            raise ValueError(
                f"attachment.{name}: no single file in project '{self._automaton.project_id}''s own "
                f"'behaviour/' folder goes by that name ({', '.join(paths) or 'none'})."
            )
        return AttachmentDoc(self._files, paths[0], self._names)
