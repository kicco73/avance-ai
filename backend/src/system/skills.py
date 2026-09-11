"""Finding what is installed, instead of naming it.

A skill is a package under `backend/src/` with a `skill.py` that declares
one Skill subclass. Nothing lists them: this walks the source tree at
boot and starts whatever is there. That is the whole mechanism, and it is
what makes a build a copying decision — leave `backend/src/listen/` out
of the package and there is no speech-to-text, with no manifest to read
at run time and no flag to keep in sync.

Deliberately not a plugin system: no versions, no dependency order, no
lifecycle beyond start and stop. Skills reach each other through the Bus,
which resolves at call time, so the order they start in does not matter.

A Skill joins the two things a package needs from the system and can ask
for nowhere else: starting its own service from the configuration file,
and putting its controllers into the router once the core exists. Both
are one object's business, because a route with no service behind it and
a service no route reaches are each half of a skill.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path
from types import ModuleType

from system import bus
from system.bus import POINT_CONFIG_SERVICES, POINT_HTTP_CONTROLLERS
from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

SKILL_MODULE = "skill"


class Skill:

    project_declarable = False

    @property
    def package(self) -> str:
        return type(self).__module__.split(".")[0]

    @property
    def key(self) -> str:
        return self.package

    @property
    def ui_label(self) -> str:
        return self.package.replace("_", " ").title()

    @property
    def ui_description(self) -> str:
        return ""

    def start(self, raw: dict, path: Path) -> None:
        self.start_service(raw, path)
        bus.contribute(POINT_CONFIG_SERVICES, self.describe_section)
        bus.contribute(POINT_HTTP_CONTROLLERS, self.register_controllers)

    def start_service(self, raw: dict, path: Path) -> None:
        pass

    def register_controllers(self, controllers: list) -> None:
        pass

    def describe_section(self, snapshot: dict) -> None:
        pass

    def section(self, fields: dict) -> dict:
        return {**fields, "ui-label": self.ui_label, "ui-description": self.ui_description}

    def stop(self) -> None:
        pass

    def required_by(self, automaton, sources: dict[str, str]) -> bool:
        return False

    def requirements(self) -> list[str]:
        return []


_skills: dict[str, Skill] = {}
_started: list[Skill] = []


def discover(source_root: Path | None = None) -> list[Skill]:
    """Every skill declared by a `<package>.skill` module importable
    under the source root, in alphabetical order. A package whose skill
    module fails to import is logged and skipped: one broken skill must
    not stop the others."""
    # src/, not this package: this module lives in system/, and what
    # it walks is the tree of packages beside system/.
    root = source_root or Path(__file__).resolve().parent.parent
    found = []
    for entry in sorted(pkgutil.iter_modules([str(root)])):
        if not entry.ispkg or not (root / entry.name / f"{SKILL_MODULE}.py").is_file():
            continue
        try:
            found.append(_skill_of(importlib.import_module(f"{entry.name}.{SKILL_MODULE}")))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Skill %r could not be imported and is skipped: %s", entry.name, exc)
    return found


def _skill_of(module: ModuleType) -> Skill:
    return _skills.setdefault(module.__name__, _declared_class(module)())


def _declared_class(module: ModuleType) -> type[Skill]:
    declared = [
        member
        for member in vars(module).values()
        if inspect.isclass(member)
        and issubclass(member, Skill)
        and member.__module__ == module.__name__
    ]
    if not declared:
        raise TypeError(f"{module.__name__} declares no Skill subclass.")
    return declared[0]


def installed(source_root: Path | None = None) -> list[dict]:
    """What a build can choose to leave out, as the Build view lists it:
    the package name — which is also the directory a build either copies
    or does not — and the same `ui_label`/`ui_description` the skill
    shows under Manage services, so one service reads as one thing in
    both places. Derived from what is on disk, so a skill added tomorrow
    appears without anyone maintaining a list."""
    return [
        {
            "key": skill.key,
            "package": skill.package,
            "ui_label": skill.ui_label,
            "ui_description": skill.ui_description,
            "declarable": bool(skill.project_declarable),
        }
        for skill in discover(source_root)
    ]


def requirements_of(packages: list[str], source_root: Path | None = None) -> list[str]:
    """The dependency lines that belong to the named packages. A skill
    owns its own directory — that is what a build copies or does not —
    but `requirements.txt` is shared, so the lines in it that exist only
    for one skill have to be claimed by that skill or they belong to
    nobody, and a product built without Talk still asks for piper-tts."""
    wanted = set(packages)
    return [
        line
        for skill in discover(source_root)
        if skill.package in wanted
        for line in skill.requirements()
    ]


def declarable(source_root: Path | None = None) -> list[dict]:
    """The services a project may declare a level for in its own
    index.yml (`project.services` — see automaton/project_services.py).
    A skill says so itself, with project_declarable: the platform, the
    build service and the compiled-product server are things an operator
    installs, never things a project asks for."""
    return [entry for entry in installed(source_root) if entry["declarable"]]


def required_for(automaton, sources: dict[str, str], source_root: Path | None = None) -> list[str]:
    """The packages this project cannot run without: what it declared as
    `required`, plus what each skill works out for itself from what the
    project does (`required_by`) — a `task.send_mail` call requires mail
    whether or not anyone wrote it down. Nothing here knows what any
    skill does: the rule lives in the skill, and a skill added tomorrow
    brings its own rule with it.

    Both forms of the project are offered because they answer different
    questions: the built `automaton` for what it does (its task scripts),
    and its `sources` for what it asked for — a default the builder
    filled in is not the project asking."""
    declared = _declared(automaton).required_keys()
    return [
        skill.package
        for skill in discover(source_root)
        if skill.key in declared or skill.required_by(automaton, sources)
    ]


def disabled_for(automaton, sources: dict[str, str], source_root: Path | None = None) -> list[str]:
    """The packages this project declared it will not use, and the ones
    it contradicts itself about — a project that declares `mail:
    disabled` and still calls task.send_mail gets the call bounced at
    run time (see automaton/project_services.py), which is worth saying
    out loud in the Build view rather than discovering in a log."""
    declared = _declared(automaton).disabled_keys()
    return [skill.package for skill in discover(source_root) if skill.key in declared]


def contradicted_for(automaton, sources: dict[str, str], source_root: Path | None = None) -> list[str]:
    declared = _declared(automaton).disabled_keys()
    return [
        skill.package
        for skill in discover(source_root)
        if skill.key in declared and skill.required_by(automaton, sources)
    ]


def _declared(automaton):
    from automaton.project_services import ProjectServices

    return getattr(automaton, "services", None) or ProjectServices()


def start_all(raw: dict, path: Path, source_root: Path | None = None) -> list[Skill]:
    """Starts every discovered skill with the configuration file as it
    was read — nothing else, because nothing else exists yet. A skill
    that needs a core object collects it later from
    bus.POINT_CORE_SERVICES, which is what keeps this signature from
    growing a parameter per skill. Each reads the section that belongs
    to it; one that finds its section absent registers nothing and says
    so."""
    for skill in discover(source_root):
        try:
            skill.start(raw, path)
            _started.append(skill)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Skill %r failed to start: %s", skill.package, exc)
    return list(_started)


async def stop_all() -> None:
    """Awaits a stop() that needs it — a skill holding an open client
    releases it the same way main.py's own shutdown does, rather than
    leaving it to process exit."""
    for skill in reversed(_started):
        try:
            for pending in filter(inspect.isawaitable, [skill.stop()]):
                await pending
        except Exception as exc:  # noqa: BLE001
            logger.exception("Skill %r failed to stop: %s", skill.package, exc)
    _started.clear()


def _reset_for_tests() -> None:
    _started.clear()
