"""The directory of compiled packages, and how one is imported.

Shared by the two halves that must agree on it: `BuildService`, which
writes a package there, and `CompiledAutomatonLoader`, which reads one
back. Neither owns the convention — this module does.

One directory per (project, stored revision), never one per project.
That is not tidiness: a Python package imported once stays in
`sys.modules` under its own name, so replacing the files under a stable
name would keep serving the module already imported, and `reload` on a
generated package is fragile. A new revision is a new directory, and so
a new module, and the question does not arise.

Nothing here goes on `sys.path`. A package is imported by file path,
under a name unique to its (project, revision), so two revisions of the
same project can be loaded at once without one answering for the other.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from typing import Any

from logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

# A build writes here first, and one rename moves the finished package
# to the name the loader reads. Without that, a directory exists under
# its final name with half its files in it and whoever looks first
# imports that. The prefix keeps a staging directory out of the way of
# `<module>.<revision>`, so nothing can mistake one for a package.
STAGING_PREFIX = ".building."


class PackageError(Exception):
    """A directory that is not a usable compiled package. Never fatal to
    a caller that can fall back to the interpreted automaton."""


def package_dir_name(module_name: str, revision: int) -> str:
    return f"{module_name}.{revision}"


def package_dir(apps_dir: Path, module_name: str, revision: int) -> Path:
    return apps_dir / package_dir_name(module_name, revision)


def staging_dir(apps_dir: Path, module_name: str, revision: int) -> Path:
    """Where a build assembles the package before anything can load it.
    `compile_contents` names what it writes after the module, so this is
    the *parent* it writes into: the package itself lands at
    `staging_dir(...) / module_name`."""
    return apps_dir / f"{STAGING_PREFIX}{package_dir_name(module_name, revision)}"


def import_automaton(directory: Path, project_id: str, revision: int) -> Any:
    """The AUTOMATON of the package in `directory`, checked against the
    revision it is being loaded for. Raises PackageError for anything
    that makes the package unusable — missing, unimportable, no
    AUTOMATON, or compiled from a different revision than the one asked
    for. The directory's own name is a convenience; STORAGE_REVISION,
    which the package itself declares, is what is trusted."""
    init = directory / "__init__.py"
    if not init.is_file():
        raise PackageError(f"{directory}: no __init__.py — not a compiled package.")

    # Unique per (project, revision), and stable across runs: two
    # revisions of one project may be imported at the same time (a
    # session pinned to the older one), and neither may find the other in
    # sys.modules. Derived rather than hashed so the name in a traceback
    # says which project it came from.
    slug = "".join(character if character.isalnum() else "_" for character in project_id)
    module_name = f"_avance_app_{slug}_{revision}"

    def forget() -> None:
        # __init__.py's own relative import (of its <name>.py, which
        # itself does `from . import prompt`) resolves its submodules
        # through sys.modules under module_name's own dotted prefix — a
        # stale one there (a past call with this same (project, revision)
        # key, since two different builds can share it across calls) would
        # otherwise be reused instead of re-imported, silently serving
        # old content. The outer entry alone isn't enough to purge.
        for cached in [name for name in sys.modules if name == module_name or name.startswith(f"{module_name}.")]:
            del sys.modules[cached]

    forget()
    spec = importlib.util.spec_from_file_location(
        module_name, init, submodule_search_locations=[str(directory)],
    )
    if spec is None or spec.loader is None:
        raise PackageError(f"{directory}: cannot be imported as a package.")
    module = importlib.util.module_from_spec(spec)
    # Registered before exec_module: see forget()'s own docstring above —
    # the same reasoning applies going forward, not just on purge.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        forget()
        raise PackageError(f"{directory}: failed to import — {exc}") from exc

    automaton = getattr(module, "AUTOMATON", None)
    if automaton is None:
        forget()
        raise PackageError(f"{directory}: imports, but declares no AUTOMATON.")
    declared = getattr(module, "STORAGE_REVISION", None)
    if declared != revision:
        forget()
        raise PackageError(
            f"{directory}: compiled from revision {declared!r}, loaded for revision {revision}."
        )
    return automaton


def discard_other_revisions(apps_dir: Path, module_name: str, keep_revision: int) -> list[Path]:
    """Every other build of the same project, gone — older revisions and
    any half-written directory left by a failed build. Called once the
    new one is in place and has been proven to import, never before."""
    keep = package_dir_name(module_name, keep_revision)
    removed = []
    for pattern in (f"{module_name}.*", f"{STAGING_PREFIX}{module_name}.*"):
        for candidate in sorted(apps_dir.glob(pattern)):
            if not candidate.is_dir() or candidate.name == keep:
                continue
            shutil.rmtree(candidate, ignore_errors=True)
            removed.append(candidate)
    return removed
