from __future__ import annotations

from pathlib import Path

from automaton.file_types import (  # noqa: F401  (re-exported: the project package reads the layout's own names from here)
    ASPECT_DIR, BEHAVIOUR_DIR, MEDIA_DIR, MEDIA_EXTENSIONS, ROOT_FILE_NAMES, ProjectFileTypes,
)

LEGAL_TERMS_FILE_NAME = "legal/terms.md"
LEGAL_TERMS_SKELETON = """# Terms of this application

These are the specific terms of this application, in addition to the
platform's general Terms of Use and Privacy Policy.

## What this application does with your data

[Describe here what data this application collects and what it is used for.]

## Permissions requested

[Describe here the specific permissions this application needs, if any.]

## Retention

[State here how long this application's data is retained.]
"""

SESSIONS_EXPORT_FILENAME = "sessions.json"
TESTS_EXPORT_FILENAME = "tests.json"
BUNDLE_FILE_NAMES = {SESSIONS_EXPORT_FILENAME, TESTS_EXPORT_FILENAME}
SOURCES_DIR = "sources"
CACHE_DIR = "cache"


class ArchiveLayout:
    """Where a project's files live and how their bytes are represented —
    canonicalizing an uploaded/imported name into this project's own
    layout (root, aspect/, behaviour/, legal/, media/), and decoding text
    archives for parsing."""

    @staticmethod
    def canonicalize_name(name: str) -> str:
        basename = Path(name).name
        if basename in ROOT_FILE_NAMES:
            if name != basename:
                raise ValueError(f"'{basename}' must be at the project root, not '{name}'.")
            return basename
        if name == LEGAL_TERMS_FILE_NAME:
            return name
        parts = Path(name).parts
        if len(parts) == 2 and parts[0] == SOURCES_DIR and Path(basename).suffix.lower() == ".csv":
            return name
        if len(parts) == 2 and parts[0] == MEDIA_DIR and Path(basename).suffix.lower() in MEDIA_EXTENSIONS:
            return name
        return ProjectFileTypes.of(basename).canonical_name(basename)

    @staticmethod
    def decode_text(archives: dict[str, bytes]) -> dict[str, str | bytes]:
        decoded: dict[str, str | bytes] = {}
        for name, content in archives.items():
            if ProjectFileTypes.of(name).text and isinstance(content, (bytes, bytearray)):
                decoded[name] = content.decode("utf-8")
            else:
                decoded[name] = content
        return decoded
