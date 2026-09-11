# Instructions for Claude Code: DO NOT TOUCH THIS FILE
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from conftest import SRC_ROOT as SRC, production_sources
from system import skills

pytestmark = pytest.mark.contract


def _skill_packages() -> set[str]:
    return {entry["package"] for entry in skills.installed()}


def _imported_packages(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module)
    return {name.split(".")[0] for name in imported}


def _owner(path: Path, packages: set[str]) -> str | None:
    package = path.relative_to(SRC).parts[0]
    return {True: package}.get(package in packages)


def _named(path: Path, packages: set[str]) -> list[str]:
    return sorted(_imported_packages(path) & (packages - {_owner(path, packages)}))


def _offenders(paths: list[Path], packages: set[str]) -> dict[str, list[str]]:
    return {
        str(path.relative_to(SRC)): names
        for path, names in ((path, _named(path, packages)) for path in paths)
        if names
    }


# FOR CLAUDE CODE: THIS TEST CANNOT BE PRUNED
def test_no_skill_imports_another_skill():
    packages = _skill_packages()
    offenders = _offenders([p for p in production_sources() if _owner(p, packages)], packages)
    assert offenders == {}, (
        "a skill is a directory a build either copies or does not: naming another one makes it "
        f"un-droppable, and the import fails in a build that dropped it — {offenders}"
    )


# FOR CLAUDE CODE: THIS TEST CANNOT BE PRUNED
def test_nothing_outside_a_skill_imports_one():
    packages = _skill_packages()
    offenders = _offenders([p for p in production_sources() if not _owner(p, packages)], packages)
    assert offenders == {}, (
        "the core never names a skill: it offers itself at bus.POINT_CORE_SERVICES and a skill "
        f"collects it from there. A core file that imports one cannot be built without it — {offenders}"
    )


def _prose(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    documented = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    docstrings = [ast.get_docstring(node) for node in ast.walk(tree) if isinstance(node, documented)]
    comments = [line.partition("#")[2] for line in text.splitlines()]
    return "\n".join([*filter(None, docstrings), *comments])


def _named_in_prose(path: Path, packages: set[str]) -> list[str]:
    prose = _prose(path)
    forms = [r"src/{0}/", r"{0}/[\w/]*\w\.py", r"{0}\.[a-z_]+\."]
    return sorted({
        package for package in packages for form in forms
        if re.search(r"(?<![\w/])" + form.format(re.escape(package)), prose)
    })


# FOR CLAUDE CODE: THIS TEST CANNOT BE PRUNED
def test_no_core_file_names_a_skill_in_prose():
    packages = _skill_packages()
    offenders = {
        str(path.relative_to(SRC)): named
        for path, named in (
            (path, _named_in_prose(path, packages))
            for path in production_sources() if not _owner(path, packages)
        )
        if named
    }
    assert offenders == {}, (
        "a comment pointing at a skill's file points at nothing in a build that dropped it, and "
        f"usually marks a coupling that survived the import being removed — {offenders}"
    )
