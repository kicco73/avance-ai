from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from automaton.automaton import Automaton
from automaton.automaton_yaml_editor import AutomatonYamlEditor
from automaton.automaton_builder import AutomatonBuilder
from automaton.build_error import AutomatonBuildError
from db import Db
from logging_factory import LoggerFactory
from session import Session
from tracking.session_import import SessionImportManager

from .archive.automaton_loader import AutomatonLoader
from .archive.layout import (
    IMAGE_CONTENT_TYPE_BY_EXTENSION, IMAGE_EXTENSIONS, SESSIONS_EXPORT_FILENAME, TESTS_EXPORT_FILENAME,
    TEXT_CONTENT_TYPE_BY_EXTENSION,
)
from .archive.zip_importer import ZipImporter
from .project_import_bundle_job import ProjectImportBundleJob
from .types import FAMILY_NOT_CHECKED, CommitCallback

if TYPE_CHECKING:
    from .manager import ProjectManager

logger = LoggerFactory.get_logger(__name__)

NEW_PROJECT_TEMPLATE = Path(__file__).resolve().parents[2] / "samples" / "projects" / "Hello world.zip"


class ProjectUploader:
    def __init__(
        self, db: Db, automaton_loader: AutomatonLoader, session_import_manager: SessionImportManager,
        manager: "ProjectManager",
    ) -> None:
        self._db = db
        self._automaton_loader = automaton_loader
        self._session_import_manager = session_import_manager
        self._manager = manager

    def extract_upload_files(
        self, content: bytes, content_type: str | None,
    ) -> tuple[dict[str, str | bytes], list[dict], list[dict]]:
        if ZipImporter.looks_like_zip(content_type, content):
            with tempfile.TemporaryDirectory() as tmp:
                staging_dir = Path(tmp)
                ZipImporter.extract_safely(content, staging_dir)
                files = {
                    file.relative_to(staging_dir).as_posix(): (
                        file.read_bytes() if file.suffix.lower() in IMAGE_EXTENSIONS
                        else file.read_text(encoding="utf-8")
                    )
                    for file in staging_dir.rglob("*") if file.is_file()
                }
        else:
            files = {"index.yml": content.decode("utf-8")}
        raw_sessions = files.pop(SESSIONS_EXPORT_FILENAME, None)
        assert not isinstance(raw_sessions, bytes)
        sessions_to_import = self._parse_json_array(raw_sessions, SESSIONS_EXPORT_FILENAME, "sessions")
        raw_tests = files.pop(TESTS_EXPORT_FILENAME, None)
        assert not isinstance(raw_tests, bytes)
        tests_to_import = self._parse_json_array(raw_tests, TESTS_EXPORT_FILENAME, "test results")
        return files, sessions_to_import, tests_to_import

    @staticmethod
    def _parse_json_array(raw: str | None, filename: str, what: str) -> list[dict]:
        if raw is None:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"'{filename}' is not valid JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise ValueError(f"'{filename}' must be a JSON array of {what}.")
        return parsed

    def _build_from_upload(
        self, content: bytes, content_type: str | None, *, force_project_id: str | None = None,
    ) -> tuple[Automaton, dict[str, str | bytes], list[dict], list[dict]]:
        try:
            files, sessions_to_import, tests_to_import = self.extract_upload_files(content, content_type)
            index_yml = files.get("index.yml")
            if not isinstance(index_yml, str):
                raise ValueError("Upload must contain an 'index.yml'.")
            if force_project_id is not None:
                editor = AutomatonYamlEditor(index_yml)
                editor.set_project_field("id", force_project_id)
                files["index.yml"] = index_yml = editor.serialize()
            declared_id, declared_family, _ = AutomatonBuilder.read_declared_env_keys(index_yml)
            if declared_id is None:
                raise ValueError(
                    "project.id is required and must be a valid identifier "
                    "(letters, digits, underscores, not starting with a digit)."
                )
            automaton = AutomatonBuilder().build(
                files, self._automaton_loader.known_projects_env_keys(declared_id, declared_family)
            )
        except AutomatonBuildError:
            raise
        except (zipfile.BadZipFile, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            logger.exception(exc)
            raise ValueError(f"Invalid project definition: {exc}") from exc
        return automaton, files, sessions_to_import, tests_to_import

    async def put_project(
        self, content: bytes, content_type: str | None, commit: CommitCallback
    ) -> tuple[dict, ProjectImportBundleJob]:
        automaton, files, sessions_to_import, tests_to_import = self._build_from_upload(content, content_type)
        project_id = automaton.project_id
        existing_published = (
            self._db.get_project_published_revision(project_id) if self._db.project_exists(project_id) else None
        )
        old_family = (
            self._automaton_loader.declared_family(project_id) if existing_published is not None else FAMILY_NOT_CHECKED
        )
        declared_revision = AutomatonBuilder.peek_declared_revision(files["index.yml"])

        if existing_published is not None:
            if declared_revision is not None and declared_revision <= existing_published:
                raise ValueError(
                    f"Project '{project_id}': uploaded revision {declared_revision} is not newer than the "
                    f"currently published revision {existing_published} — import rejected."
                )
            final_revision = declared_revision if declared_revision is not None else existing_published + 1
        else:
            final_revision = declared_revision if declared_revision is not None else 0

        return await self._persist_uploaded_project(
            project_id, final_revision, automaton, files, sessions_to_import, tests_to_import, commit,
            old_family=old_family,
        )

    async def _persist_uploaded_project(
        self, project_id: str, revision: int, automaton: Automaton, files: dict[str, str | bytes],
        sessions_to_import: list[dict], tests_to_import: list[dict], commit: CommitCallback,
        *, old_family: str | None | object = FAMILY_NOT_CHECKED,
    ) -> tuple[dict, ProjectImportBundleJob]:
        files_bytes = {
            name: value.encode("utf-8") if isinstance(value, str) else value
            for name, value in files.items()
        }
        content_types = {
            name: (
                IMAGE_CONTENT_TYPE_BY_EXTENSION[Path(name).suffix.lower()]
                if Path(name).suffix.lower() in IMAGE_EXTENSIONS
                else TEXT_CONTENT_TYPE_BY_EXTENSION.get(Path(name).suffix.lower(), "text/plain")
            )
            for name in files
        }
        is_new_project = not self._db.project_exists(project_id)
        if not is_new_project:
            self._db.reset_project(project_id)
        self._db.import_new_revision(project_id, revision, files_bytes, content_types)
        self._db.set_active_project_id(project_id, Session().user)
        await self._manager.finalize_update(
            project_id, automaton, commit, is_new_project=is_new_project, old_family=old_family,
        )
        self._manager.publish_project(project_id)

        job = ProjectImportBundleJob(
            self._session_import_manager, self._db, project_id, sessions_to_import, tests_to_import
        )
        return {"success": True, "project_id": project_id}, job

    def _unique_project_id(self, base: str) -> str:
        existing = set(self._db.list_projects())
        if base not in existing:
            return base
        suffix = 2
        while f"{base}_{suffix}" in existing:
            suffix += 1
        return f"{base}_{suffix}"

    async def create_new_project(self, commit: CommitCallback) -> tuple[dict, ProjectImportBundleJob]:
        content = NEW_PROJECT_TEMPLATE.read_bytes()
        template_files, _, _ = self.extract_upload_files(content, "application/zip")
        base_id, _, _ = AutomatonBuilder.read_declared_env_keys(template_files["index.yml"])
        project_id = self._unique_project_id(base_id or "hello_world")
        automaton, files, sessions_to_import, tests_to_import = self._build_from_upload(
            content, "application/zip", force_project_id=project_id,
        )
        return await self._persist_uploaded_project(
            project_id, automaton.project_revision, automaton, files, sessions_to_import, tests_to_import, commit,
        )
