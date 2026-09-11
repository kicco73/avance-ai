"""The single table of what a project file's extension means.

Every other view of file types is derived from here: the media type the
runtime reader and the AI-facing attachment loader report
(automaton.media_types), the content type an upload is stored with, the
folder it canonicalizes into and the size it may not exceed
(project.archive.layout), and the catalog the frontend reads over
/api/core/projects/file-types instead of hardcoding its own patterns.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ASPECT_DIR = "aspect"
BEHAVIOUR_DIR = "behaviour"
ROOT_FILE_NAMES = {"index.yml", "index.css"}

TEXT_MEDIA_TYPE = "text/plain"
DEFAULT_MEDIA_TYPE = "application/octet-stream"

UNLIMITED_UPLOAD_BYTES = sys.maxsize
MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_AUDIO_UPLOAD_BYTES = 15 * 1024 * 1024

IMAGE_KIND = "image"
AUDIO_KIND = "audio"


@dataclass(frozen=True)
class ProjectFileType:
    extension: str
    content_type: str
    label: str
    kind: str
    text: bool
    max_upload_bytes: int
    folder: str

    @property
    def media_type(self) -> str:
        return TEXT_MEDIA_TYPE if self.text else self.content_type

    def canonical_name(self, basename: str) -> str:
        return f"{self.folder}/{basename}"

    def oversized(self, size: int) -> bool:
        return size > self.max_upload_bytes

    def payload(self) -> dict:
        return {
            "extension": self.extension,
            "content_type": self.content_type,
            "label": self.label,
            "kind": self.kind,
            "folder": self.folder,
            "text": self.text,
            "max_upload_bytes": self.max_upload_bytes,
        }


@dataclass(frozen=True)
class RootProjectFileType(ProjectFileType):
    folder: str = ""

    def canonical_name(self, basename: str) -> str:
        if basename in ROOT_FILE_NAMES:
            return basename
        raise ValueError(f"'{basename}' is only allowed at the project root, as {' or '.join(sorted(ROOT_FILE_NAMES))}.")


@dataclass(frozen=True)
class UnknownProjectFileType(ProjectFileType):
    extension: str = ""
    content_type: str = DEFAULT_MEDIA_TYPE
    label: str = "File"
    kind: str = "unknown"
    text: bool = False
    max_upload_bytes: int = 0
    folder: str = ""

    @property
    def media_type(self) -> str:
        return DEFAULT_MEDIA_TYPE

    def canonical_name(self, basename: str) -> str:
        extension = Path(basename).suffix.lower()
        raise ValueError(f"Unsupported file extension for '{basename}': '{extension or '(none)'}'.")

    def oversized(self, size: int) -> bool:
        return False

    def payload(self) -> dict:
        raise ValueError("The unknown file type is never part of the published catalog.")


class ProjectFileTypes:

    _TYPES = (
        RootProjectFileType(".yml", "text/yaml", "YAML", "definition", True, UNLIMITED_UPLOAD_BYTES),
        RootProjectFileType(".yaml", "text/yaml", "YAML", "definition", True, UNLIMITED_UPLOAD_BYTES),
        ProjectFileType(".txt", "text/plain", "Text", "document", True, UNLIMITED_UPLOAD_BYTES, BEHAVIOUR_DIR),
        ProjectFileType(".md", "text/markdown", "Markdown", "document", True, UNLIMITED_UPLOAD_BYTES, BEHAVIOUR_DIR),
        ProjectFileType(".csv", "text/csv", "CSV", "data", True, UNLIMITED_UPLOAD_BYTES, BEHAVIOUR_DIR),
        ProjectFileType(".css", "text/css", "Stylesheet", "stylesheet", True, UNLIMITED_UPLOAD_BYTES, ASPECT_DIR),
        ProjectFileType(".png", "image/png", "PNG image", IMAGE_KIND, False, MAX_IMAGE_UPLOAD_BYTES, ASPECT_DIR),
        ProjectFileType(".jpg", "image/jpeg", "JPEG image", IMAGE_KIND, False, MAX_IMAGE_UPLOAD_BYTES, ASPECT_DIR),
        ProjectFileType(".jpeg", "image/jpeg", "JPEG image", IMAGE_KIND, False, MAX_IMAGE_UPLOAD_BYTES, ASPECT_DIR),
        ProjectFileType(".gif", "image/gif", "GIF image", IMAGE_KIND, False, MAX_IMAGE_UPLOAD_BYTES, ASPECT_DIR),
        ProjectFileType(".webp", "image/webp", "WebP image", IMAGE_KIND, False, MAX_IMAGE_UPLOAD_BYTES, ASPECT_DIR),
        ProjectFileType(".svg", "image/svg+xml", "SVG image", IMAGE_KIND, False, MAX_IMAGE_UPLOAD_BYTES, ASPECT_DIR),
        ProjectFileType(".mp3", "audio/mpeg", "MP3 audio", AUDIO_KIND, False, MAX_AUDIO_UPLOAD_BYTES, ASPECT_DIR),
    )

    UNKNOWN = UnknownProjectFileType()

    _BY_EXTENSION = {file_type.extension: file_type for file_type in _TYPES}

    @classmethod
    def all(cls) -> tuple[ProjectFileType, ...]:
        return cls._TYPES

    @classmethod
    def of(cls, name: str) -> ProjectFileType:
        return cls._BY_EXTENSION.get(Path(name).suffix.lower(), cls.UNKNOWN)

    @classmethod
    def extensions_in(cls, folder: str) -> set[str]:
        return {file_type.extension for file_type in cls._TYPES if file_type.folder == folder}

    @classmethod
    def extensions_of_kind(cls, kind: str) -> set[str]:
        return {file_type.extension for file_type in cls._TYPES if file_type.kind == kind}

    @classmethod
    def text_extensions(cls) -> set[str]:
        return {file_type.extension for file_type in cls._TYPES if file_type.text}

    @classmethod
    def binary_extensions(cls) -> set[str]:
        return {file_type.extension for file_type in cls._TYPES if not file_type.text}

    @classmethod
    def content_types_by_extension(cls) -> dict[str, str]:
        return {file_type.extension: file_type.content_type for file_type in cls._TYPES}

    @classmethod
    def catalog_payload(cls) -> dict:
        return {
            "root_file_names": sorted(ROOT_FILE_NAMES),
            "types": [file_type.payload() for file_type in cls._TYPES],
        }
