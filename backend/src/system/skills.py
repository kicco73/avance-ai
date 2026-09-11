"""Finding what is installed, instead of naming it.

A skill is a package under `backend/src/` with a `skill.py` that exposes
`start(raw, path)`. Nothing lists them: this walks the source tree at
boot and starts whatever is there. That is the whole mechanism, and it is
what makes a build a copying decision — leave `backend/src/listen/` out
of the package and there is no speech-to-text, with no manifest to read
at run time and no flag to keep in sync.

Deliberately not a plugin system: no versions, no dependency order, no
lifecycle beyond start and stop. Skills reach each other through the Bus,
which resolves at call time, so the order they start in does not matter.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path
from types import ModuleType

from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

SKILL_MODULE = "skill"

_started: list[ModuleType] = []


def discover(source_root: Path | None = None) -> list[ModuleType]:
    """Every `<package>.skill` importable under the source root, in
    alphabetical order. A package whose skill module fails to import is
    logged and skipped: one broken skill must not stop the others."""
    # src/, not this package: this module lives in system/, and what
    # it walks is the tree of packages beside system/.
    root = source_root or Path(__file__).resolve().parent.parent
    found = []
    for entry in sorted(pkgutil.iter_modules([str(root)])):
        if not entry.ispkg or not (root / entry.name / f"{SKILL_MODULE}.py").is_file():
            continue
        try:
            found.append(importlib.import_module(f"{entry.name}.{SKILL_MODULE}"))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Skill %r could not be imported and is skipped: %s", entry.name, exc)
    return found


def installed(source_root: Path | None = None) -> list[dict]:
    """What a build can choose to leave out, as the Build view lists it:
    the package name — which is also the directory a build either copies
    or does not — and the same `UI_LABEL`/`UI_DESCRIPTION` the service
    shows under Manage services, so one service reads as one thing in
    both places. Derived from what is on disk, so a skill added tomorrow
    appears without anyone maintaining a list."""
    return [
        {
            "key": getattr(module, "KEY", module.__name__.split(".")[0]),
            "package": module.__name__.split(".")[0],
            "ui_label": getattr(module, "UI_LABEL", module.__name__.split(".")[0].replace("_", " ").title()),
            "ui_description": getattr(module, "UI_DESCRIPTION", ""),
        }
        for module in discover(source_root)
    ]


def required_for(automaton, sources: dict[str, str], source_root: Path | None = None) -> list[str]:
    """The packages this project cannot run without. Nothing here knows
    what any of them do: each installed skill is handed the project and
    answers for itself (`required_by`), so the rule that `task.send_mail`
    needs mail lives in mail, and a skill added tomorrow brings its own
    rule with it. A skill that never declares one is never required.

    Both forms of the project are offered because they answer different
    questions: the built `automaton` for what it does (its task scripts),
    and its `sources` for what it asked for — a default the builder
    filled in is not the project asking."""
    return [
        module.__name__.split(".")[0]
        for module in discover(source_root)
        for required_by in filter(None, [getattr(module, "required_by", None)])
        if required_by(automaton, sources)
    ]


def start_all(raw: dict, path: Path, source_root: Path | None = None) -> list[ModuleType]:
    """Starts every discovered skill with the configuration file as it
    was read — nothing else, because nothing else exists yet. A skill
    that needs a core object collects it later from
    bus.POINT_CORE_SERVICES, which is what keeps this signature from
    growing a parameter per skill. Each reads the section that belongs
    to it; one that finds its section absent registers nothing and says
    so."""
    for module in discover(source_root):
        try:
            module.start(raw, path)
            _started.append(module)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Skill %r failed to start: %s", module.__name__, exc)
    return list(_started)


async def stop_all() -> None:
    """Awaits a stop() that needs it — a skill holding an open client
    releases it the same way main.py's own shutdown does, rather than
    leaving it to process exit."""
    for module in reversed(_started):
        for stop in filter(None, [getattr(module, "stop", None)]):
            try:
                for pending in filter(inspect.isawaitable, [stop()]):
                    await pending
            except Exception as exc:  # noqa: BLE001
                logger.exception("Skill %r failed to stop: %s", module.__name__, exc)
    _started.clear()


def _reset_for_tests() -> None:
    _started.clear()
