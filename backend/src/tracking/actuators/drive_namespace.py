from __future__ import annotations

from automaton.automaton import Automaton
from automaton.file_types import DEFAULT_MEDIA_TYPE, ProjectFileTypes
from db import Db
from system import bus
from system.bus import OUTPUT_DRIVE, Message

from .actuator_set import _run_sync

TEXT_MEDIA_TYPE = "text/plain"


class DriveNamespace:
    def __init__(self, db: Db, automaton: Automaton, username: str, session_id: int | None = None) -> None:
        self._db = db
        self._automaton = automaton
        self._username = username
        self._session_id = session_id

    @staticmethod
    def _path(path: str) -> str:
        cleaned = "/".join(segment for segment in str(path).split("/") if segment)
        if not cleaned:
            raise ValueError(f"drive path '{path}' names no file.")
        if any(segment in (".", "..") for segment in cleaned.split("/")):
            raise ValueError(f"drive path '{path}': '..' and '.' have nothing to point at here.")
        return cleaned

    @staticmethod
    def _content_type(path: str, *, binary: bool) -> str:
        file_type = ProjectFileTypes.of(path)
        if binary:
            return DEFAULT_MEDIA_TYPE if file_type.text else file_type.content_type
        return file_type.content_type if file_type.text else TEXT_MEDIA_TYPE

    def _project_id(self) -> str:
        project_id = self._automaton.project_id
        if project_id is None:
            raise ValueError("drive: this automaton has no project to write a drive for.")
        return project_id

    def read(self, path: str) -> str | bytes:
        found = self._db.read_drive_file(self._project_id(), self._username, self._path(path))
        if found is None:
            return ""
        content, content_type = found
        return content.decode("utf-8") if content_type.startswith("text/") else content

    def write(self, path: str, content: str | bytes) -> str:
        resolved = self._path(path)
        if isinstance(content, str):
            payload, content_type = content.encode("utf-8"), self._content_type(resolved, binary=False)
        elif isinstance(content, (bytes, bytearray)):
            payload, content_type = bytes(content), self._content_type(resolved, binary=True)
        else:
            raise ValueError(
                f"drive.write('{path}'): only text or bytes can be written here — got {type(content).__name__}."
            )
        project_id = self._project_id()
        self._db.write_drive_file(project_id, self._username, resolved, payload, content_type, self._session_id)
        _run_sync(bus.publish(Message(
            type=OUTPUT_DRIVE, username=self._username, project_id=project_id,
            session_id=self._session_id, body={"path": resolved},
        )))
        return resolved

    def list(self, prefix: str = "") -> list[str]:
        files = self._db.list_drive_files(self._project_id(), self._username, str(prefix))
        return [entry["path"] for entry in files]

    def delete(self, path: str) -> bool:
        return self._db.delete_drive_file(self._project_id(), self._username, self._path(path))


class NoDriveNamespace(DriveNamespace):

    def __init__(self) -> None:
        pass

    def read(self, path: str) -> str:
        return ""

    def write(self, path: str, content: str | bytes) -> str:
        raise ValueError(f"drive.write('{path}'): this build carries no storage for a drive.")

    def list(self, prefix: str = "") -> list[str]:
        return []

    def delete(self, path: str) -> bool:
        return False


def drive_namespace_for(
    db: "Db | None", automaton: Automaton, username: str | None, session_id: int | None = None,
) -> DriveNamespace:
    if db is None or automaton.project_id is None or username is None:
        return NoDriveNamespace()
    return DriveNamespace(db, automaton, username, session_id)
