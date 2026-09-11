"""What a project file's own name says about its type.

A two-line table, but it decides whether a file can be read as text at
all, so both the builder that converts archives and the run-time reader
that serves them must agree on it. It lives here, with the model rather
than with the builder, so that reading a project's files never drags in
the YAML-building chain a compiled product does not ship. The rows
themselves come from automaton.file_types, the one catalog the upload
and editing side reads too.
"""
from __future__ import annotations

from automaton.file_types import DEFAULT_MEDIA_TYPE, ProjectFileTypes

EXTENSION_TO_MEDIA_TYPE = {
    file_type.extension: file_type.media_type for file_type in ProjectFileTypes.all()
}


def media_type_for(filename: str) -> str:
    return ProjectFileTypes.of(filename).media_type
