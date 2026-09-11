"""The path says who answers it, and that is checked rather than remembered.

Every route lives under /api/skills/<key>/ for the skill that registers
it, or under /api/core/ when no skill does. That is what makes a 404
readable: /api/skills/talk/* not answering is a build without src/talk/,
not a bug to investigate, and the same fact is already true of the
directory a build copies or does not (see system/skills.py).

It held by convention until now, and convention is exactly what it could
not survive: /api/chat/ was taken to mean webchat's, and over time three
other packages registered there — the editor's inspector, the labelling
screen, and talk. Two of the chat window's own routes ended up in the
labelling controller, which a build without an editor does not have.
Nothing failed, because in a full build every package is present.

Read statically, off the decorators, so a controller whose service
cannot be constructed is still checked.
"""
from __future__ import annotations

import ast
from pathlib import Path

from system import skills

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
ROUTE_DECORATORS = {"get", "post", "put", "delete", "route"}
CORE_PREFIX = "/api/core/"
SKILLS_COLLECTION = "/api/skills"


def declared_routes(path: Path) -> list[tuple[str, str]]:
    """(function name, route path) for every decorated method in one
    file. `route` carries the method as its first argument, so the path
    is the second — everything else takes it first."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        for decorator in getattr(node, "decorator_list", []):
            found += _route_of(decorator, node)
    return found


def _route_of(decorator, node) -> list[tuple[str, str]]:
    call = decorator if isinstance(decorator, ast.Call) else None
    name = getattr(getattr(call, "func", None), "id", None)
    args = [a for a in getattr(call, "args", []) if isinstance(a, ast.Constant)]
    wanted = args[1:] if name == "route" else args[:1]
    return [(node.name, wanted[0].value) for _ in [1] if name in ROUTE_DECORATORS and wanted]


def controller_files() -> list[Path]:
    return sorted(
        path for path in SRC_ROOT.rglob("*_controller.py")
        if "tests" not in path.parts and path.name != "base_controller.py"
    )


def package_of(path: Path) -> str:
    return path.relative_to(SRC_ROOT).parts[0]


def prefix_for(package: str) -> str:
    """The prefix a controller in this package may declare under: its
    skill's key, or /api/core/ when the package declares no skill."""
    keys = {entry["package"]: entry["key"] for entry in skills.installed()}
    return f"/api/skills/{keys[package]}/" if package in keys else CORE_PREFIX


def test_every_route_lives_under_the_prefix_of_whoever_answers_it():
    wrong = [
        (path.relative_to(SRC_ROOT), function, route, prefix_for(package_of(path)))
        for path in controller_files()
        for function, route in declared_routes(path)
        if not route.startswith(prefix_for(package_of(path))) and route != SKILLS_COLLECTION
    ]
    assert not wrong, "\n".join(
        f"{file}::{function} declares {route!r}, which is not under {prefix}"
        for file, function, route, prefix in wrong
    )


def test_every_controller_declares_at_least_one_route():
    """A controller file with no route left in it is a move that only got
    half done — the routes went somewhere and the file stayed."""
    empty = [path.relative_to(SRC_ROOT) for path in controller_files() if not declared_routes(path)]
    assert not empty, f"controllers with no routes: {empty}"


def test_the_skills_collection_is_answered_by_core():
    """GET /api/skills is the one route outside every skill prefix, and
    it has to be: it is the list of which prefixes exist at all, so a
    build that left out the package answering it could not be asked what
    it is."""
    owners = {
        package_of(path)
        for path in controller_files()
        for _, route in declared_routes(path)
        if route == SKILLS_COLLECTION
    }
    packages = {entry["package"] for entry in skills.installed()}
    assert owners and not (owners & packages), f"{SKILLS_COLLECTION} answered by {owners}"
