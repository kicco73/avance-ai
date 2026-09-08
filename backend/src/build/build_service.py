"""Builds a stored project into a compiled package, for the Build view.

The one build this does today is "local module": the project's own
Archive rows are compiled into a package written inside this one, next to
compiler.py, so the result is importable as `build.<name>` without moving
anything. The Target step's other options (zip, push to a repository) are
not wired to anything yet and this service knows nothing about them.

All of this is scaffolding for testing the round trip through the panel,
not the shape the feature will keep.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from logging_factory import LoggerFactory

from .compiler import CompileError, compile_contents

if TYPE_CHECKING:
    from db import Db
    from project.project_service import ProjectService

logger = LoggerFactory.get_logger(__name__)

# Where a locally built module lands: inside this package.
BUILD_DIR = Path(__file__).resolve().parent

# Names this package already uses for something else, so a project whose
# id sanitizes to one of them cannot quietly overwrite it.
_RESERVED = frozenset({"compiler", "build_service", "data"})


def module_name_for(project_id: str) -> str:
    """`project_id` as a Python package name. Project ids are dot-
    segmented and may carry hyphens, neither of which is legal here."""
    name = "".join(character if character.isalnum() or character == "_" else "_" for character in project_id)
    if name[:1].isdigit():
        name = f"_{name}"
    if not name.isidentifier():
        raise CompileError(f"project id {project_id!r} cannot be turned into a package name.")
    if name in _RESERVED:
        raise CompileError(f"project id {project_id!r} would overwrite build/{name}.py — rename the project.")
    return name


class BuildService:
    def __init__(self, db: "Db", project_service: "ProjectService") -> None:
        self._db = db
        self._project_service = project_service

    def build_local_module(self, project_id: str) -> dict:
        """Compiles `project_id`'s published revision into build/<name>/
        and reports what was written. Raises CompileError with a message
        the panel can show as-is."""
        revision = self._project_service.get_published_revision(project_id)
        archives = self._db.get_archives(project_id, revision=revision)
        if not archives:
            raise CompileError(f"Project '{project_id}' has no files at revision {revision}.")
        from project.archive.layout import ArchiveLayout

        module_name = module_name_for(project_id)
        package_dir = compile_contents(ArchiveLayout.decode_text(archives), module_name, BUILD_DIR)
        logger.info("Built project '%s' revision %s into %s", project_id, revision, package_dir)
        return {
            "module": f"build.{module_name}",
            "path": str(package_dir),
            "revision": revision,
            "files": sorted(path.name for path in package_dir.iterdir() if path.is_file()),
        }
