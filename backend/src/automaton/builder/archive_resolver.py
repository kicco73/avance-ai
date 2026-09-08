"""The project's own files, as the *builder* sees them.

Build time is the only place that both knows every file the project
carries and has its bytes in hand, so it is where a declared
`attachments:` name is turned into the stored path it means — an exact
match, or a unique basename — and where the few checks that must look
inside a file (attachment.read's text-only/size limits) happen.

What comes out of here is a name, never a file: an Automaton carries the
paths its declarations resolved to, and the bytes are read per turn
through tracking.project_files.ProjectFiles. That is what lets the same
declaration mean "this Archive row" on the platform and "this file under
data/" in a compiled package, with nothing in the model that knows which.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

from automaton.media_types import EXTENSION_TO_MEDIA_TYPE, media_type_for  # noqa: F401  (re-exported: project/editor.py imports EXTENSION_TO_MEDIA_TYPE from here)


class ProjectArchives:
    """Every file this project carries, by stored path. Built once per
    build from the same `contents` the YAML itself came out of."""

    def __init__(self, contents: Mapping[str, str | bytes]) -> None:
        self._contents: dict[str, str | bytes] = dict(contents)

    def declare_missing(self, path: str) -> None:
        """A file a `sources:` entry points at but the project doesn't
        carry yet — a CSV not uploaded so far. Known by name from here on
        (so declaring it as an attachment isn't a build error), with no
        content behind it."""
        self._contents.setdefault(path, "")

    def names(self) -> list[str]:
        return list(self._contents)

    def find(self, declared: str, for_field: str) -> str | None:
        """The stored path `declared` means, or None if the project has no
        such file. Same resolution ProjectFiles uses at run time — with
        the ambiguity that one can only answer None to raised here, where
        there is a line of YAML to blame it on."""
        if declared in self._contents:
            return declared
        matches = [path for path in self._contents if Path(path).name == declared]
        if len(matches) > 1:
            raise ValueError(
                f"{for_field} attachment named '{declared}' is ambiguous — "
                f"matches {', '.join(sorted(matches))}"
            )
        return matches[0] if matches else None

    def require(self, declared_names: Iterable[str], for_field: str) -> tuple[str, ...]:
        """Every declared name as the stored path it resolves to, in
        declaration order. Existence is verified here and nowhere else:
        run time reads what this returned and does not look for it again."""
        paths = []
        for declared in declared_names:
            resolved = self.find(declared, for_field)
            if resolved is None:
                raise ValueError(f"{for_field} attachment named '{declared}' not found")
            paths.append(resolved)
        return tuple(paths)

    def media_type(self, path: str) -> str:
        return media_type_for(path)

    def text(self, path: str) -> str | None:
        """The file's text, or None if it isn't a text file — the same
        text/plain rule the run-time reader applies, decided from the
        extension in exactly one place (automaton.media_types)."""
        if self.media_type(path) != "text/plain":
            return None
        content = self._contents.get(path)
        if isinstance(content, (bytes, bytearray)):
            return bytes(content).decode("utf-8", errors="replace")
        return content or ""
