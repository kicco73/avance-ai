"""A test drives a public entry point and observes a public result.

The a-priori half of CLAUDE.md's "What a test may look at": reading the
suite's own AST, with nothing constructed. A test that calls a private
method or reads a private attribute fails on a rename and holds when the
product breaks — and one of them here could not fail at all. It patched
`tracking_service._metrics`, which the code path under test never uses,
so its assertion passed whether the gate worked, was inverted, or was
deleted.

This counted 240 reaches across 56 files when it was written. What is
left is declared by the test that does it, in a module-level
`REACHES_INTO` mapping each private member to the reason it cannot be
expressed against the public surface. The declaration lives in the file
rather than in a list here for the same reason a skill's tests live in
its package: a central list names, to every customer, the skills a build
left out — and it rots, because a file it names can leave while the
entry stays behind. A reach nobody declared fails here, and a
declaration whose test stopped needing it fails here too.

Name mangling is not what holds this line. GeminiProvider already
mangles `__build_contents` and the tests reached in anyway, spelling it
`provider._GeminiProvider__build_contents  # type: ignore`. That buys a
deterrent and a visible diff; this test is the gate.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from conftest import SRC_ROOT

pytestmark = pytest.mark.contract

TESTS_ROOT = Path(__file__).resolve().parent
SAMPLES_TESTS_ROOT = TESTS_ROOT.parent / "samples" / "tests"

#: Named test seams, not reach-ins: each is a reset the harness owns and
#: the process needs between tests (see conftest's autouse fixtures).
SEAMS = {"_reset_for_tests"}

DECLARATION = "REACHES_INTO"


def _test_files() -> list[Path]:
    return sorted(
        [p for p in SRC_ROOT.rglob("tests/test_*.py")]
        + [p for p in TESTS_ROOT.glob("test_*.py")]
        + [p for p in SAMPLES_TESTS_ROOT.glob("test_*.py")]
    )


def _parsed(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _reaches(tree: ast.Module) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not node.attr.startswith("_") or node.attr.startswith("__") or node.attr in SEAMS:
            continue
        owner = node.value
        # A test's own fakes are its business; only another object's
        # privates are a reach into the implementation.
        if isinstance(owner, ast.Name) and owner.id == "self":
            continue
        found.add(node.attr)
    return found


def _declared(tree: ast.Module) -> dict[str, str]:
    """What the file says about itself: the literal value of its
    module-level REACHES_INTO, read rather than imported, so a suite of
    1500 tests is checked without starting any of them."""
    for node in tree.body:
        targets = getattr(node, "targets", [])
        if any(isinstance(target, ast.Name) and target.id == DECLARATION for target in targets):
            return ast.literal_eval(node.value)
    return {}


def _relative(path: Path) -> str:
    for root, prefix in ((SRC_ROOT, "src"), (TESTS_ROOT, "tests")):
        if root in path.parents or root == path.parent:
            return f"{prefix}/{path.relative_to(root).as_posix()}"
    return path.as_posix()


def test_no_test_reaches_into_an_implementation_it_did_not_declare():
    offenders = {}
    for path in _test_files():
        tree = _parsed(path)
        unexplained = _reaches(tree) - set(_declared(tree))
        if unexplained:
            offenders[_relative(path)] = sorted(unexplained)
    assert not offenders, (
        "These tests read or call another object's private members. Drive the public entry "
        "point and observe the public result instead (CLAUDE.md, 'What a test may look at'). "
        f"If one genuinely has no public form, declare it in that file's {DECLARATION} with "
        f"its reason: {offenders}"
    )


def test_every_declaration_is_still_needed():
    stale = {}
    for path in _test_files():
        tree = _parsed(path)
        unused = sorted(set(_declared(tree)) - _reaches(tree))
        if unused:
            stale[_relative(path)] = unused
    assert not stale, (
        f"These {DECLARATION} entries are no longer used by the test that needed them. Delete "
        f"them — a declaration is only allowed to shrink: {stale}"
    )
