"""The concrete LLM providers are private to the `ai` package: the only
way the rest of the app reaches a model is ai.AiService. A provider
imported anywhere else would mean a second, unmanaged client — with its
own event-loop and retry hazards (see test_provider_event_loops.py) —
so the boundary is checked, not assumed."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from conftest import SRC_ROOT as SRC, production_sources

pytestmark = pytest.mark.contract


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
    for path in production_sources():
        if path.is_relative_to(SRC / "ai"):
            continue
        leaks = {name for name in _imported_modules(path) if name.startswith("ai._providers")}
        if leaks:
            offenders[str(path.relative_to(SRC))] = sorted(leaks)
    assert offenders == {}, f"concrete providers imported outside ai/: {offenders}"


_PUBLIC_SUBPACKAGES = ("ai.turn", "ai.ai_talker")
"""The AI turn machine's own implementation — moved out of core so a
build can drop the whole `ai/` directory and lose it, not folded back
into `ai/__init__.py` (that would force every caller of `AiService` to
import a `TrackingProcessorAfterAiMessage` it never asked for). `ai.turn`
and `ai.ai_talker` are a second, narrower public surface: reachable, but
only as themselves — `ai._providers` stays fully private, checked above."""


def test_the_public_surface_is_the_only_thing_the_app_imports_from_ai():
    """Consumers import from `ai` itself or from `ai.turn`/`ai.ai_talker`
    (the turn machine's own implementation) — never a provider, never a
    deeper path than that, so the package can rearrange everything below
    those two seams without touching them."""
    offenders = {}
    for path in production_sources():
        if path.is_relative_to(SRC / "ai"):
            continue
        submodules = {
            name for name in _imported_modules(path)
            if name.startswith("ai.") and not any(name == allowed or name.startswith(f"{allowed}.") for allowed in _PUBLIC_SUBPACKAGES)
        }
        if submodules:
            offenders[str(path.relative_to(SRC))] = sorted(submodules)
    assert offenders == {}, f"ai submodules imported outside ai/, ai/turn/ or ai/ai_talker.py: {offenders}"
