"""The `media` namespace — `media.<doc_id>.url()` returns the download
URL for one of this project's own files uploaded under its `media/`
folder, `doc_id` derived from the file's name (see
automaton.file_types.media_doc_id_for — the same derivation
AutomatonValidator's own build-time reference check and
ProjectInspector's own autocomplete registry use, so a name valid in one
is valid, and known, in all three). Reachable only from an action's own
on-exit script (see IdentifierRegistry.for_on_exit) — there is no reason
to hand out a download link anywhere else the way attachment.<doc_id>.read() hands
out a file's own text.

The url itself is the same one every other reader of a project's files
already serves off (ProjectController.get_project_file_content) — a
plain path, not an origin: the frontend resolves it the same way it
already resolves index.css's own url(...) references (see
cssAssetUrls.js)."""
from __future__ import annotations

from typing import Any

from automaton.automaton import Automaton
from automaton.file_types import media_doc_id_for
from tracking.project_files import ProjectFiles


class MediaDoc:
    def __init__(self, project_id: str, path: str) -> None:
        self._project_id = project_id
        self._path = path

    def url(self) -> str:
        return f"/api/core/projects/{self._project_id}/files/{self._path}/content"


class MediaNamespace:
    def __init__(self, files: ProjectFiles, automaton: Automaton) -> None:
        self._files = files
        self._automaton = automaton

    def _doc_paths(self) -> dict[str, str]:
        paths: dict[str, str] = {}
        for name in self._files.names():
            doc_id = media_doc_id_for(name)
            if doc_id is not None:
                paths[doc_id] = name
        return paths

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        path = self._doc_paths().get(name)
        if path is None:
            raise ValueError(
                f"media.{name}: no such file uploaded in this project's own 'media/' folder."
            )
        return MediaDoc(self._automaton.project_id, path)
