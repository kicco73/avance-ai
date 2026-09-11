from __future__ import annotations

from importlib.metadata import packages_distributions
from pathlib import Path

import pytest

from system import skills

pytestmark = pytest.mark.contract

BACKEND_DIR = Path(__file__).resolve().parent.parent
SRC = BACKEND_DIR / "src"
REQUIREMENTS = BACKEND_DIR / "requirements.txt"


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _distribution(line: str) -> str:
    for separator in (">=", "==", "<=", "~=", ">", "<", "["):
        line = line.split(separator)[0]
    return line.strip().replace("_", "-").lower()


def _modules_by_distribution() -> dict[str, set[str]]:
    by_distribution: dict[str, set[str]] = {}
    for module, distributions in packages_distributions().items():
        for distribution in distributions:
            by_distribution.setdefault(distribution.replace("_", "-").lower(), set()).add(module)
    return by_distribution


def _packages_importing(modules: set[str]) -> set[str]:
    importing = set()
    for path in SRC.rglob("*.py"):
        if "tests" in path.relative_to(SRC).parts:
            continue
        text = path.read_text(errors="replace")
        if any(f"import {module}" in text for module in modules):
            importing.add(path.relative_to(SRC).parts[0])
    return importing


def _claims() -> dict[str, list[str]]:
    claims: dict[str, list[str]] = {}
    for skill in skills.discover():
        for line in skill.requirements():
            claims.setdefault(line, []).append(skill.package)
    return claims


def test_every_line_a_skill_claims_is_really_in_the_shared_file():
    present = _lines(REQUIREMENTS.read_text())
    missing = [line for line in _claims() if line not in present]

    assert missing == [], f"claimed by a skill but absent from requirements.txt: {missing}"


def test_no_two_skills_claim_the_same_dependency():
    shared = {line: owners for line, owners in _claims().items() if len(owners) > 1}

    assert shared == {}, f"a line cannot leave with one skill and stay with another: {shared}"


def test_a_dependency_only_one_skill_imports_is_claimed_by_that_skill():
    packages = {skill.package for skill in skills.discover()}
    by_distribution = _modules_by_distribution()
    claims = _claims()
    unclaimed = []
    for line in _lines(REQUIREMENTS.read_text()):
        modules = by_distribution.get(_distribution(line))
        if not modules:
            continue
        importers = _packages_importing(modules)
        if importers <= packages and len(importers) == 1 and line not in claims:
            unclaimed.append((line, next(iter(importers))))

    assert unclaimed == [], (
        "imported by one skill and by nothing else, yet claimed by nobody, "
        f"so a build without that skill still asks pip for it: {unclaimed}"
    )


def test_requirements_of_answers_only_for_the_packages_it_is_given():
    # Each skill owns its own lines (see _claims), so the question a build
    # asks is answered package by package: what one skill claims is in the
    # answer when it is named, and in nothing else's.
    claims = _claims()
    owned = {}
    for line, (package,) in ((line, owners) for line, owners in claims.items() if len(owners) == 1):
        owned.setdefault(package, set()).add(line)
    assert owned, "no skill in this build claims a dependency of its own"

    assert skills.requirements_of([]) == []
    for package, lines in owned.items():
        assert set(skills.requirements_of([package])) == lines
    assert set(skills.requirements_of(list(owned))) == set().union(*owned.values())

    claimants = {package for owners in claims.values() for package in owners}
    for skill in skills.discover():
        if skill.package not in claimants:
            assert skills.requirements_of([skill.package]) == []
