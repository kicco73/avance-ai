from __future__ import annotations

from pathlib import Path

# -- Project file layout -------------------------------------------------
# Shared schema for how a project's files are named, typed, and where each
# extension lives on disk. Consumed by ArchiveLayout below and directly by
# editor.py/manager.py wherever they need this data without an operation
# to go with it.

TEXT_EDITABLE_EXTENSIONS = {".yml", ".yaml", ".txt", ".md", ".csv", ".css"}

LEGAL_TERMS_FILE_NAME = "legal/terms.md"

# Seeded by ProjectEditor.add_legal_terms into a fresh legal/terms.md —
# per-app terms shown once, on top of the platform's own general Terms of
# Service (see backend/src/docs/TERMS.md).
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

TEXT_CONTENT_TYPE_BY_EXTENSION = {
    ".yml": "text/yaml",
    ".yaml": "text/yaml",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".css": "text/css",
}

IMAGE_CONTENT_TYPE_BY_EXTENSION = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}
IMAGE_EXTENSIONS = set(IMAGE_CONTENT_TYPE_BY_EXTENSION)

MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024

SESSIONS_EXPORT_FILENAME = "sessions.json"
TESTS_EXPORT_FILENAME = "tests.json"
BUNDLE_FILE_NAMES = {SESSIONS_EXPORT_FILENAME, TESTS_EXPORT_FILENAME}

ASPECT_DIR = "aspect"
BEHAVIOUR_DIR = "behaviour"
ROOT_FILE_NAMES = {"index.yml", "index.css"}
ASPECT_EXTENSIONS = IMAGE_EXTENSIONS | {".css"}
BEHAVIOUR_EXTENSIONS = {".txt", ".md", ".csv"}


class ArchiveLayout:
    """Where a project's files live and how their bytes are represented —
    canonicalizing an uploaded/imported name into this project's own
    layout (root, aspect/, behaviour/, legal/), and decoding text archives
    for parsing."""

    @staticmethod
    def canonicalize_name(name: str) -> str:
        basename = Path(name).name
        if basename in ROOT_FILE_NAMES:
            if name != basename:
                raise ValueError(f"'{basename}' must be at the project root, not '{name}'.")
            return basename
        if name == LEGAL_TERMS_FILE_NAME:
            return name
        extension = Path(basename).suffix.lower()
        if extension in ASPECT_EXTENSIONS:
            return f"{ASPECT_DIR}/{basename}"
        if extension in BEHAVIOUR_EXTENSIONS:
            return f"{BEHAVIOUR_DIR}/{basename}"
        raise ValueError(f"Unsupported file extension for '{name}': '{extension or '(none)'}'.")

    @staticmethod
    def decode_text(archives: dict[str, bytes]) -> dict[str, str | bytes]:
        decoded: dict[str, str | bytes] = {}
        for name, content in archives.items():
            if Path(name).suffix.lower() in TEXT_EDITABLE_EXTENSIONS and isinstance(content, (bytes, bytearray)):
                decoded[name] = content.decode("utf-8")
            else:
                decoded[name] = content
        return decoded
