"""The concrete LLM providers are private to the `ai` package: the only
way the rest of the app reaches a model is ai.AiService. A provider
imported anywhere else would mean a second, unmanaged client — with its
own event-loop and retry hazards (see test_provider_event_loops.py) —
so the boundary is checked, not assumed."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

SRC = Path(__file__).resolve().parent.parent / "src"


def _imported_modules(path: Path) -> set[str]:
    """Every module a file imports — `from ai import X` counts as `ai`,
    `from ai.llm_provider import X` as `ai.llm_provider`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def test_nothing_outside_the_ai_package_imports_a_provider():
    offenders = {}
    for path in SRC.rglob("*.py"):
        if path.is_relative_to(SRC / "ai"):
            continue
        leaks = {name for name in _imported_modules(path) if name.startswith("ai._providers")}
        if leaks:
            offenders[str(path.relative_to(SRC))] = sorted(leaks)
    assert offenders == {}, f"concrete providers imported outside ai/: {offenders}"


def test_the_public_surface_is_the_only_thing_the_app_imports_from_ai():
    """Consumers import from `ai` itself, never a submodule — so the
    package can rearrange its insides without touching them."""
    offenders = {}
    for path in SRC.rglob("*.py"):
        if path.is_relative_to(SRC / "ai"):
            continue
        submodules = {name for name in _imported_modules(path) if name.startswith("ai.")}
        if submodules:
            offenders[str(path.relative_to(SRC))] = sorted(submodules)
    assert offenders == {}, f"ai submodules imported outside ai/: {offenders}"
