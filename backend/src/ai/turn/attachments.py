"""A turn's own attachments, read when the turn is built.

An automaton carries the paths its `attachments:` declarations resolved
to (see automaton.builder.archive_resolver) and never the bytes, so the
files themselves are read here, once per turn, through whichever
ProjectFiles that automaton composes — Archive rows on the platform, the
package's own data/ in a compiled product, both behind the same
byte-bounded cache.

What a file becomes is decided from its name alone (automaton.
media_types), never from the media type the reader reports: a stored
project's Archive row carries the type the uploader stamped on it
(text/csv for a CSV) while a package's own data/ has only the file, so
letting the reader decide would send the same attachment as text in one
world and as base64 in the other. The table here is the same one the
builder used when the automaton still carried the converted files, so
what reaches the provider is byte-for-byte what it was.
"""
from __future__ import annotations

import base64
from typing import Iterable, TYPE_CHECKING

from automaton.media_types import media_type_for
from automaton.model import MemoryArchive, SourceDict
from system.logging_factory import LoggerFactory

if TYPE_CHECKING:
    from tracking.project_files import ProjectFiles

logger = LoggerFactory.get_logger(__name__)


def archive_of(path: str, content: bytes) -> MemoryArchive:
    media_type = media_type_for(path)
    if media_type == "text/plain":
        source: SourceDict = {"type": "text", "media_type": "text/plain", "data": content.decode("utf-8")}
    else:
        source = {"type": "base64", "media_type": media_type, "data": base64.b64encode(content).decode("ascii")}
    return MemoryArchive(filename=path, source=source)


def load_attachments(files: "ProjectFiles", paths: Iterable[str]) -> list[MemoryArchive]:
    """Every declared path, in declaration order. A path that no longer
    resolves to a stored file is dropped with a warning rather than
    raising: existence was verified when the project was built, so this
    can only mean the project changed underneath a session, and a turn
    that still works minus one attachment beats a turn that fails."""
    archives = []
    for path in paths:
        found = files.read(path)
        if found is None:
            logger.warning("Attachment '%s' is declared but no longer stored — skipped for this turn.", path)
            continue
        archives.append(archive_of(path, found[0]))
    return archives
