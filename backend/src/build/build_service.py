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

import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from logging_factory import LoggerFactory

from .apps import PackageError, discard_other_revisions, import_automaton, package_dir, staging_dir
from .compiler import CompileError, compile_contents

if TYPE_CHECKING:
    from db import Db
    from project.project_service import ProjectService

logger = LoggerFactory.get_logger(__name__)

# Kept for the CLI, which writes a package wherever it is told to. A
# build from the panel goes to the configured apps directory instead.
BUILD_DIR = Path(__file__).resolve().parent

BACKEND_DIR = BUILD_DIR.parent.parent
REPO_ROOT = BACKEND_DIR.parent
BUILDS_DIR = REPO_ROOT / "builds"
STAGING_PREFIX = ".building."

_BACKEND_COPY_IGNORE = shutil.ignore_patterns(
    ".venv", "__pycache__", "*.pyc", "*.egg-info", "apps", "*.db", "*.sqlite", "*.sqlite3",
)

_LAUNCH_TIMEOUT_SECONDS = 20.0


def _ignore_for(excluded_skills: "list[str] | None"):
    """What not to copy: the usual build leftovers, plus the source
    directory of every skill this build leaves out. That directory *is*
    the switch — nothing else records the choice, and there is nothing to
    read at run time to discover it (see skills.py). The launch check at
    the end of the build is what proves the remaining code still stands
    up without it."""
    excluded = set(excluded_skills or ())

    def ignore(directory: str, names: list[str]) -> set[str]:
        dropped = set(_BACKEND_COPY_IGNORE(directory, names))
        if excluded and Path(directory).resolve() == (BACKEND_DIR / "src").resolve():
            dropped |= {name for name in names if name in excluded}
        return dropped

    return ignore

# Names this package already uses for something else, so a project whose
# id sanitizes to one of them cannot quietly overwrite it.
_RESERVED = frozenset({"compiler", "build_service", "data"})


def published_revision_of(db: "Db", project_service: "ProjectService", project_id: str) -> int:
    published = project_service.get_published_revision(project_id)
    current = db.get_project_revision(project_id)
    if current != published:
        raise CompileError(
            f"Project '{project_id}' has unpublished changes (draft revision {current}, "
            f"published {published}) — publish them first."
        )
    return published


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
    def __init__(self, db: "Db", project_service: "ProjectService", apps_dir: Path) -> None:
        self._db = db
        self._project_service = project_service
        self._apps_dir = apps_dir

    def build_local_module(self, project_id: str) -> dict:
        """Compiles `project_id`'s published revision into the apps
        directory and reports what was written. Raises CompileError with a
        message the panel can show as-is."""
        revision = published_revision_of(self._db, self._project_service, project_id)
        archives = self._db.get_archives(project_id, revision=revision)
        if not archives:
            raise CompileError(f"Project '{project_id}' has no files at revision {revision}.")
        from project.archive.layout import ArchiveLayout

        module_name = module_name_for(project_id)
        staging = staging_dir(self._apps_dir, module_name, revision)
        final = package_dir(self._apps_dir, module_name, revision)
        self._apps_dir.mkdir(parents=True, exist_ok=True)
        # Whatever a previous failed build left behind, gone before this
        # one writes a single file into the same place.
        shutil.rmtree(staging, ignore_errors=True)
        try:
            built = compile_contents(ArchiveLayout.decode_text(archives), module_name, staging, revision)
            # Proven where the loader cannot see it. A package that does
            # not import is a failed build, never something to publish.
            try:
                import_automaton(built, project_id, revision)
            except PackageError as exc:
                raise CompileError(f"The package built for '{project_id}' does not load: {exc}") from exc
            shutil.rmtree(final, ignore_errors=True)
            built.rename(final)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        # The entry cached for this revision, if any, is the interpreted
        # automaton — the compiled one must take its place from the next
        # load on.
        self._project_service.invalidate_automaton(project_id, revision)
        discarded = discard_other_revisions(self._apps_dir, module_name, revision)
        logger.info(
            "Built project '%s' revision %s into %s (dropped %s).",
            project_id, revision, final, ", ".join(path.name for path in discarded) or "nothing",
        )
        return {
            "module": final.name,
            "path": str(final),
            "revision": revision,
            "files": sorted(path.name for path in final.iterdir() if path.is_file()),
        }

    def build_backend_copy(self, project_id: str, excluded_skills: "list[str] | None" = None) -> dict:
        revision = published_revision_of(self._db, self._project_service, project_id)
        module_name = module_name_for(project_id)
        target_name = f"{module_name}.{revision}"
        final = BUILDS_DIR / target_name
        staging = BUILDS_DIR / f"{STAGING_PREFIX}{target_name}"

        BUILDS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        try:
            backend_copy = staging / "backend"
            shutil.copytree(BACKEND_DIR, backend_copy, ignore=_ignore_for(excluded_skills))
            self._write_compiled_automaton(project_id, revision, module_name, backend_copy)
            self._write_pruned_database(project_id, backend_copy)
            self._verify_backend_copy_launches(backend_copy)
            shutil.rmtree(final, ignore_errors=True)
            staging.rename(final)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        logger.info(
            "Built backend copy of '%s' revision %s into %s (without: %s).",
            project_id, revision, final / "backend", ", ".join(excluded_skills or []) or "nothing",
        )
        return {"path": str(final / "backend"), "revision": revision, "excluded_skills": list(excluded_skills or [])}

    def _write_compiled_automaton(self, project_id: str, revision: int, module_name: str, backend_copy: Path) -> None:
        from project.archive.layout import ArchiveLayout

        archives = self._db.get_archives(project_id, revision=revision)
        if not archives:
            raise CompileError(f"Project '{project_id}' has no files at revision {revision}.")
        apps_dir = backend_copy / "apps"
        apps_dir.mkdir(parents=True, exist_ok=True)
        package_staging = staging_dir(apps_dir, module_name, revision)
        shutil.rmtree(package_staging, ignore_errors=True)
        try:
            built = compile_contents(ArchiveLayout.decode_text(archives), module_name, package_staging, revision)
            try:
                import_automaton(built, project_id, revision)
            except PackageError as exc:
                raise CompileError(f"The package built for '{project_id}' does not load: {exc}") from exc
            built.rename(package_dir(apps_dir, module_name, revision))
        finally:
            shutil.rmtree(package_staging, ignore_errors=True)

    def _write_pruned_database(self, project_id: str, backend_copy: Path) -> None:
        db_filename = Path(self._db.backup_file_path()).name
        dest = backend_copy / "src" / db_filename
        dest.write_bytes(self._db.export_backup())
        _prune_database_to_project(dest, project_id)

    def _verify_backend_copy_launches(self, backend_copy: Path) -> None:
        python = BACKEND_DIR / ".venv" / "bin" / "python"
        port = _free_port()
        process = subprocess.Popen(
            [str(python), "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=backend_copy / "src", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            self._wait_until_responding(process, port)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def _wait_until_responding(self, process: subprocess.Popen, port: int) -> None:
        url = f"http://127.0.0.1:{port}/api/auth/providers"
        deadline = time.monotonic() + _LAUNCH_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            exit_code = process.poll()
            if exit_code is not None:
                raise CompileError(
                    f"Copied backend exited (code {exit_code}) before starting up:\n{process.stdout.read()}"
                )
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                pass
            time.sleep(0.5)
        raise CompileError(f"Copied backend did not answer at {url} within {_LAUNCH_TIMEOUT_SECONDS:.0f}s.")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _prune_database_to_project(db_path: Path, keep_project_id: str) -> None:
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        other_ids = [row[0] for row in connection.execute("SELECT id FROM Project WHERE id != ?", (keep_project_id,))]
        if not other_ids:
            return
        connection.executemany("DELETE FROM Project WHERE id = ?", [(project_id,) for project_id in other_ids])
        placeholders = ",".join("?" for _ in other_ids)
        for table, column in (
            ("StateRemap", "project_id"),
            ("EditHistory", "project_id"),
            ("Test", "project_id"),
            ("TestAggregateResult", "project_id"),
            ("SystemWarning", "project_id"),
            ("ProjectObserverIndex", "project_id"),
            ("ProjectObserverIndex", "observer_project_id"),
        ):
            connection.execute(f"DELETE FROM {table} WHERE {column} IN ({placeholders})", other_ids)
        connection.commit()
    finally:
        connection.close()
