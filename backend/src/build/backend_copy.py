"""A whole backend built around one project, one named step at a time.

The steps are the unit here, not the build: each one is a thing the
operator can see happen and a thing that can fail on its own, which is
what lets the Build view show where a build got to instead of a spinner
(see build/build_job.py, which runs them in order).

The last step runs the built backend's own test suite, and that is the
point of copying the tests in at all. A skill left out of a build takes
its tests with it — they live inside its package, under
`backend/src/<skill>/tests/` — so what runs here is exactly the suite
that belongs to what was built, and a build that leaves out a skill
nothing depends on still has to prove the rest stands up without it.
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from project.archive.packages import PackageError, discard_other_revisions, import_automaton, package_dir, staging_dir
from system.logging_factory import LoggerFactory

from .compiler import CompileError, compile_contents

if TYPE_CHECKING:
    from db import Db

logger = LoggerFactory.get_logger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = BACKEND_DIR.parent
BUILDS_DIR = REPO_ROOT / "builds"
STAGING_PREFIX = ".building."

_TEST_TIMEOUT_SECONDS = 1800.0
# The one thing a build's own test run must not do: build a backend and
# run its tests, which would do it again. The tests that do it say so
# (see pytest.ini), and this is where they are left out.
_RECURSIVE_MARKER = "spawns_a_build"
_TEST_OUTPUT_TAIL_LINES = 40

_BACKEND_COPY_IGNORE = shutil.ignore_patterns(
    ".venv", "__pycache__", "*.pyc", "*.egg-info", "apps", "*.db", "*.sqlite", "*.sqlite3",
)


@dataclass(frozen=True)
class BuildStep:
    """One visible phase of a build. `method` names what it runs on the
    BackendCopy — a name rather than the function itself, so the step
    table stays a description of the build and the work stays with the
    object that owns the state it touches."""

    key: str
    label: str
    method: str

    def run(self, copy: "BackendCopy") -> None:
        getattr(copy, self.method)()


STEPS = (
    BuildStep("copy", "Copying the backend", "copy_backend"),
    BuildStep("automaton", "Compiling the automaton", "install_automaton"),
    BuildStep("database", "Pruning the database", "prune_database"),
    BuildStep("publish", "Publishing the build", "publish"),
    BuildStep("tests", "Running the build's tests", "run_tests"),
)


class BackendCopy:
    """Everything one backend-copy build knows about itself. Constructed
    before the first step runs, which is what makes "this project has
    unpublished changes" a 400 on the request rather than a job that
    starts and then fails."""

    def __init__(
        self, db: "Db", project_id: str, revision: int, module_name: str, excluded_skills: list[str],
    ) -> None:
        self._db = db
        self.project_id = project_id
        self.revision = revision
        self.module_name = module_name
        self.excluded_skills = excluded_skills
        self._tests: dict | None = None

    @property
    def target_name(self) -> str:
        return f"{self.module_name}.{self.revision}"

    @property
    def final(self) -> Path:
        return BUILDS_DIR / self.target_name

    @property
    def staging(self) -> Path:
        return BUILDS_DIR / f"{STAGING_PREFIX}{self.target_name}"

    @property
    def assembling(self) -> Path:
        """The copy while it is being assembled: under a staging name, so
        nothing can mistake a half-written tree for a finished build."""
        return self.staging / "backend"

    @property
    def built(self) -> Path:
        """The same copy once `publish` has moved it into place. What the
        test step runs against, and what the report points at."""
        return self.final / "backend"

    def report(self) -> dict:
        return {
            "path": str(self.built),
            "revision": self.revision,
            "excluded_skills": list(self.excluded_skills),
            "tests": self._tests,
        }

    def discard(self) -> None:
        """Whatever this build had assembled but not yet published. The
        last good build of the same project stays where it was: nothing
        is dropped until `publish` has put this one in place."""
        shutil.rmtree(self.staging, ignore_errors=True)

    # --- the steps --------------------------------------------------------

    def copy_backend(self) -> None:
        BUILDS_DIR.mkdir(parents=True, exist_ok=True)
        self.discard()
        logger.info(
            "copying backend to %s (without: %s)",
            self.assembling, ", ".join(self.excluded_skills) or "nothing",
        )
        shutil.copytree(BACKEND_DIR, self.assembling, ignore=_ignore_for(self.excluded_skills))
        self._write_requirements()

    def _write_requirements(self) -> None:
        from system import skills

        dropped = set(skills.requirements_of(self.excluded_skills))
        path = self.assembling / "requirements.txt"
        kept = [line for line in path.read_text().splitlines() if line.strip() not in dropped]
        path.write_text("\n".join(kept) + "\n")

    def install_automaton(self) -> None:
        from project.archive.layout import ArchiveLayout

        archives = self._db.get_archives(self.project_id, revision=self.revision)
        if not archives:
            raise CompileError(f"Project '{self.project_id}' has no files at revision {self.revision}.")
        apps_dir = self.assembling / "apps"
        apps_dir.mkdir(parents=True, exist_ok=True)
        package_staging = staging_dir(apps_dir, self.module_name, self.revision)
        shutil.rmtree(package_staging, ignore_errors=True)
        try:
            built = compile_contents(
                ArchiveLayout.decode_text(archives), self.module_name, package_staging, self.revision,
            )
            try:
                import_automaton(built, self.project_id, self.revision)
            except PackageError as exc:
                raise CompileError(f"The package built for '{self.project_id}' does not load: {exc}") from exc
            built.rename(package_dir(apps_dir, self.module_name, self.revision))
        finally:
            shutil.rmtree(package_staging, ignore_errors=True)

    def prune_database(self) -> None:
        db_filename = Path(self._db.backup_file_path()).name
        dest = self.assembling / "src" / db_filename
        dest.write_bytes(self._db.export_backup())
        _prune_database_to_project(dest, self.project_id)

    def publish(self) -> None:
        # One directory per project, not one per build: the revision just
        # written stays, every other build of the same project goes. Same
        # rule the apps directory already follows, and for the same
        # reason — a backend copy is the whole tree, so two of them are
        # two of everything.
        shutil.rmtree(self.final, ignore_errors=True)
        self.staging.rename(self.final)
        discarded = discard_other_revisions(BUILDS_DIR, self.module_name, self.revision)
        logger.info(
            "Built backend copy of '%s' revision %s into %s (without: %s) (dropped %s).",
            self.project_id, self.revision, self.built,
            ", ".join(self.excluded_skills) or "nothing",
            ", ".join(path.name for path in discarded) or "nothing",
        )

    def run_tests(self) -> None:
        """The built backend's own suite, run against the built backend —
        the interpreter is this one's, since a copy carries no virtualenv,
        but everything collected and everything imported is the copy's.
        Which is the point: a skill left out took its tests with it, so
        what runs here is the suite of what was actually built."""
        completed = subprocess.run(
            [
                str(BACKEND_DIR / ".venv" / "bin" / "python"), "-m", "pytest", "-q",
                "-p", "no:cacheprovider", "-m", f"not {_RECURSIVE_MARKER}",
            ],
            cwd=self.built, capture_output=True, text=True, timeout=_TEST_TIMEOUT_SECONDS,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        self._tests = {
            "passed": completed.returncode == 0,
            "summary": _last_line(output),
            "output": _tail(output),
        }
        if completed.returncode != 0:
            raise CompileError(f"The build's own tests failed:\n{_tail(output)}")
        logger.info("Build of '%s' passed its own tests: %s", self.project_id, _last_line(output))


def _ignore_for(excluded_skills: list[str]):
    """What not to copy: the usual build leftovers, plus the source
    directory of every skill this build leaves out. That directory *is*
    the switch — nothing else records the choice, and there is nothing to
    read at run time to discover it (see system/skills.py). Its tests go
    with it, because they are inside it."""
    excluded = set(excluded_skills)

    def ignore(directory: str, names: list[str]) -> set[str]:
        dropped = set(_BACKEND_COPY_IGNORE(directory, names))
        if excluded and Path(directory).resolve() == (BACKEND_DIR / "src").resolve():
            dropped |= {name for name in names if name in excluded}
        return dropped

    return ignore


def _last_line(output: str) -> str:
    lines = [line for line in output.strip().splitlines() if line.strip()]
    return lines[-1] if lines else "(no output)"


def _tail(output: str) -> str:
    lines = output.strip().splitlines()
    return "\n".join(lines[-_TEST_OUTPUT_TAIL_LINES:]) or "(no output)"


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
