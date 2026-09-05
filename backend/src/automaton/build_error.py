"""The structured form of a build/validation failure — same message every
AutomatonBuilder/AutomatonLoader raise site already composed, plus where it
happened: which project, which stored file, which line (0-based, matching
CodeEditor.vue's own jumpToLine convention), which section (a human dotted
path, e.g. "states.confirm.actions.retry"). A ValueError subclass so every
existing `except ValueError` keeps working unchanged; a ServiceError
subclass so error_handlers.py's own ServiceError handler already covers it
via MRO (see service_error.py's own docstring) with no new registration.
`line`/`section` are often None — not every raise site sits inside a
per-item loop with a YAML node still in scope (see AutomatonBuilder's own
_at/_line_of); that's graceful degradation, not a bug."""
from __future__ import annotations

from service_error import ServiceError


class AutomatonBuildError(ServiceError, ValueError):
    def __init__(
        self, message: str, *,
        project_id: str | None = None, revision: int | None = None,
        file: str = "index.yml", line: int | None = None, section: str | None = None,
    ) -> None:
        super().__init__(message, status_code=400)
        self.project_id = project_id
        self.revision = revision
        self.file = file
        self.line = line
        self.section = section

    def fields(self) -> dict[str, object]:
        """Only the non-None structured fields — what error_handlers.py
        nests under body["error"]["fields"]."""
        raw = {
            "project_id": self.project_id, "revision": self.revision,
            "file": self.file, "line": self.line, "section": self.section,
        }
        return {key: value for key, value in raw.items() if value is not None}
