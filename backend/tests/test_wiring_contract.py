"""Every controller's dependencies exist, checked without building one.

This is the a-priori half of `system/wiring.py`: the resolver reports a
missing name at install time, and this says the same thing from the
source tree alone — no services, no database, no configuration, nothing
constructed. A controller that asks for something nobody offers fails
here, in milliseconds, instead of at somebody's boot.

Two wiring mistakes in this refactor would have been caught by it: a
parameter passed in the wrong position, and a controller left asking for
a service that had moved to another package.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from system import skills
from system.wiring import requirements

pytestmark = pytest.mark.contract

SRC = Path(__file__).resolve().parent.parent / "src"


def _core_service_names() -> set[str]:
    """The keys main.py contributes to bus.POINT_CORE_SERVICES, read off
    its source. Read rather than imported because importing main.py
    builds an application; what is wanted here is only what it promises."""
    tree = ast.parse((SRC / "main.py").read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = getattr(node.func, "attr", None)
        if target != "contribute" or not node.args:
            continue
        if getattr(node.args[0], "id", None) != "POINT_CORE_SERVICES":
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Dict) and inner.keys:
                return {key.value for key in inner.keys if isinstance(key, ast.Constant)}
    raise AssertionError("main.py no longer contributes a literal registry to POINT_CORE_SERVICES.")


def _controller_classes():
    """Every *Controller class under src/, with the package it lives in."""
    import importlib

    for path in sorted(SRC.rglob("*_controller.py")):
        package = path.relative_to(SRC).parts[0]
        module = importlib.import_module(
            ".".join(path.relative_to(SRC).with_suffix("").parts)
        )
        for name, member in vars(module).items():
            if name.endswith("Controller") and inspect.isclass(member) and member.__module__ == module.__name__:
                yield package, member


def _skill_service_names() -> set[str]:
    """`<KEY>_service` for every installed skill — what a skill offers
    its own controllers alongside the core registry."""
    return {f"{getattr(module, 'KEY', '')}_service" for module in skills.discover()}


def test_every_controller_asks_only_for_names_something_offers():
    offered = _core_service_names() | _skill_service_names()
    unsatisfied = {}
    for package, controller in _controller_classes():
        missing = [name for name in requirements(controller) if name not in offered]
        if missing:
            unsatisfied[f"{package}.{controller.__name__}"] = missing

    assert not unsatisfied, (
        f"These controllers ask for names nothing offers: {unsatisfied}. "
        f"Offered: {sorted(offered)}."
    )


def test_no_controller_takes_a_parameter_it_never_reads():
    """A dependency that exists only to be assigned to self is not a
    dependency. It matters more now than it did: wiring by name turns
    every parameter into a declared requirement, and this is what keeps
    a dead one from being declared forever."""
    dead = {}
    for path in sorted(SRC.rglob("*_controller.py")):
        for node in ast.parse(path.read_text()).body:
            if not isinstance(node, ast.ClassDef):
                continue
            init = next(
                (m for m in node.body if isinstance(m, ast.FunctionDef) and m.name == "__init__"), None
            )
            if init is None:
                continue
            parameters = [argument.arg for argument in init.args.args if argument.arg != "self"]
            read = {
                attribute.attr
                for attribute in ast.walk(node)
                if isinstance(attribute, ast.Attribute)
                and isinstance(attribute.value, ast.Name)
                and attribute.value.id == "self"
                and isinstance(attribute.ctx, ast.Load)
            }
            never_read = [name for name in parameters if name not in read]
            if never_read:
                dead[node.name] = never_read

    assert not dead, f"Parameters assigned and never read: {dead}"


def test_the_skills_own_service_is_named_after_the_skill():
    """`<KEY>_service`, so a skill's controllers can ask for their own
    service by a name derived from the skill rather than agreed
    per-package (see system/wiring.py)."""
    core = _core_service_names()
    for package, controller in _controller_classes():
        own = {name for name in requirements(controller) if name.endswith("_service")} - core
        if not own:
            continue
        keys = {getattr(module, "KEY", None) for module in skills.discover()
                if module.__name__.split(".")[0] == package}
        expected = {f"{key}_service" for key in keys if key}
        assert own <= expected or not expected, (
            f"{controller.__name__} asks for {sorted(own)}; "
            f"{package}'s own service is {sorted(expected)}."
        )
