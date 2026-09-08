"""What a project file's own name says about its type.

A two-line table, but it decides whether a file can be read as text at
all, so both the builder that converts archives and the run-time reader
that serves them must agree on it. It lives here, with the model rather
than with the builder, so that reading a project's files never drags in
the YAML-building chain a compiled product does not ship.
"""
from __future__ import annotations

from pathlib import Path

EXTENSION_TO_MEDIA_TYPE = {
    ".yml": "text/plain",
    ".md": "text/plain",
    ".txt": "text/plain",
    ".csv": "text/plain",
}

DEFAULT_MEDIA_TYPE = "application/octet-stream"


def media_type_for(filename: str) -> str:
    return EXTENSION_TO_MEDIA_TYPE.get(Path(filename).suffix.lower(), DEFAULT_MEDIA_TYPE)
