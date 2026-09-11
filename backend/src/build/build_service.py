"""What the Build view calls, and nothing it does itself for long.

Two builds. "Local module" compiles the project's published revision into
a package under the configured apps directory, where the compiled loader
finds it — a matter of seconds, so it stays a request that returns when
it is done. "Backend copy" is a whole backend built around one project,
which takes minutes and ends by running that backend's own tests: it is
a job with a step apiece (see build/backend_copy.py, build/build_job.py),
and this service only assembles it.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from system.logging_factory import LoggerFactory

from project.archive.packages import PackageError, discard_other_revisions, import_automaton, package_dir, staging_dir

from .backend_copy import BackendCopy
from .build_job import BuildJob
from .compiler import CompileError, compile_contents

if TYPE_CHECKING:
    from db import Db
    from project.project_service import ProjectService

logger = LoggerFactory.get_logger(__name__)

# Kept for the CLI, which writes a package wherever it is told to. A
# build from the panel goes to the configured apps directory instead.
BUILD_DIR = Path(__file__).resolve().parent

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

    def installed_skills(self, project_id: str) -> dict:
        """What this backend has installed, and what the project makes of
        each: `required` is what it cannot be built without (the Build
        view ticks those and refuses to untick them), `disabled` what it
        declared it will not use, and `contradicted` the few it declared
        disabled while still calling into them. Asked of the project's
        published automaton, so what a draft is about to add does not
        constrain a build of what is live."""
        from project.archive.layout import ArchiveLayout
        from system import skills

        revision = published_revision_of(self._db, self._project_service, project_id)
        automaton = self._project_service.get_automaton(project_id, revision)
        sources = ArchiveLayout.decode_text(self._db.get_archives(project_id, revision=revision))
        return {
            "skills": skills.installed(),
            "required": skills.required_for(automaton, sources),
            "disabled": skills.disabled_for(automaton, sources),
            "contradicted": skills.contradicted_for(automaton, sources),
        }

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

    def backend_copy_job(self, project_id: str, excluded_skills: "list[str] | None" = None) -> BuildJob:
        """The build itself, as a job somebody watches run (see
        build/build_job.py). Everything that can be known before the first
        step runs is settled here — the published revision, the package
        name — so a project that cannot be built says so on the request
        rather than in a job that starts and then stops."""
        return BuildJob(BackendCopy(
            self._db,
            project_id,
            published_revision_of(self._db, self._project_service, project_id),
            module_name_for(project_id),
            list(excluded_skills or []),
        ))
