"""Build's own section of the configuration file.

`project-service.compiled-automaton` is read here rather than in
config.py because it is a question only this package can answer: whether
to serve a project's published revision from a compiled package when one
exists, falling back to the interpreted automaton otherwise. A backend
without this package has never built a package and has no such choice to
make, so it should not carry the setting that makes it.

The key still sits under `project-service` because that is where it has
always been and an existing config.yml should keep working. It would
read better under `build-service`, which already carries apps-dir —
worth doing, but as its own change rather than smuggled into this one.
"""
from __future__ import annotations

from pathlib import Path


def serves_compiled(raw: dict, path: Path) -> bool:
    """An on/off switch and nothing more: which package answers for which
    project and revision is decided per load, never here. Configurable
    rather than inferred from apps-dir's contents so the interpreted path
    can be forced for debugging, and so a forgotten apps-dir cannot
    quietly take over."""
    section = raw.get("project-service") or {}
    if not isinstance(section, dict):
        raise ValueError(f"{path}: 'project-service' section is not a mapping.")
    value = section.get("compiled-automaton", False)
    if not isinstance(value, bool):
        raise ValueError(
            f"{path}: 'project-service.compiled-automaton' must be true or false, not {value!r}."
        )
    return value
