from __future__ import annotations

import base64
from pathlib import Path

from automaton.automaton import MemoryArchive, SourceDict

EXTENSION_TO_MEDIA_TYPE = {
    ".yml": "text/plain",
    ".md": "text/plain",
    ".txt": "text/plain",
    ".csv": "text/plain",
}


class ArchiveResolver:
    @staticmethod
    def convert_contents_to_archives(contents: dict) -> dict[str, MemoryArchive]:
        archives = {}
        for archive_name, archive_contents in contents.items():
            extension = Path(archive_name).suffix.lower()
            media_type = EXTENSION_TO_MEDIA_TYPE.get(extension, "application/octet-stream")
            if media_type == "text/plain":
                source: SourceDict = {"type": "text", "media_type": "text/plain", "data": archive_contents}
            else:
                if isinstance(archive_contents, str):
                    data_bytes = archive_contents.encode("utf-8")
                elif isinstance(archive_contents, (bytes, bytearray)):
                    data_bytes = archive_contents
                else:
                    raise TypeError(
                        f"Contenuto di '{archive_name}' non valido: atteso str o bytes, "
                        f"ricevuto {type(archive_contents).__name__}"
                    )
                encoded = base64.b64encode(data_bytes).decode("ascii")
                source = {"type": "base64", "media_type": media_type, "data": encoded}
            archives[archive_name] = MemoryArchive(filename=archive_name, source=source)
        return archives

    @staticmethod
    def find_archive(required_attachment: str, all_archives: dict[str, MemoryArchive], for_field: str) -> str | None:
        if required_attachment in all_archives:
            return required_attachment
        matches = [archive_name for archive_name in all_archives if Path(archive_name).name == required_attachment]
        if len(matches) > 1:
            raise ValueError(
                f"{for_field} attachment named '{required_attachment}' is ambiguous — "
                f"matches {', '.join(sorted(matches))}"
            )
        return matches[0] if matches else None

    @classmethod
    def extract_required_archives(
        cls, required_attachments: list[str], all_archives: dict[str, MemoryArchive], for_field: str,
    ) -> dict[str, MemoryArchive]:
        extracted_archives = {}
        for required_attachment in required_attachments:
            resolved = cls.find_archive(required_attachment, all_archives, for_field)
            if resolved is None:
                raise ValueError(f"{for_field} attachment named '{required_attachment}' not found")
            extracted_archives[required_attachment] = all_archives[resolved]
        return extracted_archives
