"""Building a collaborator from a registry, by the names it asks for.

`construct(SomeController, registry)` reads the parameter names off
`__init__` and hands each one the entry of that name. Nothing else: it
knows no services, no skills, no core, and holds no list of anything.
The registry is whatever the caller passes.

Why by name. A controller's constructor was called positionally from its
skill, which meant the skill had to know seven signatures and keep them
in order — and two objects of compatible type in the wrong order is not
an error anybody sees. Twice in this refactor a platform_service went
where a project_service belonged and nothing complained. Resolving by
name cannot make that mistake: a parameter is asked for by the same word
the registry files it under.

What it does not do. It does not construct services — those take
configuration and other things a registry has no business carrying —
only the collaborators whose every argument is something already
composed. It does not decide anything when a name is missing: it raises
saying which, and the caller decides whether that is a bug or a build
that legitimately left a package out.
"""
from __future__ import annotations

import inspect
from typing import Any


class MissingDependencies(Exception):
    """A class asked for names the registry does not have. Carries them
    all rather than the first, so one boot reports one list."""

    def __init__(self, cls: type, missing: list[str], offered: list[str]) -> None:
        self.cls = cls
        self.missing = missing
        self.offered = offered
        super().__init__(
            f"{cls.__name__} needs {', '.join(missing)}, which nothing offers. "
            f"Available: {', '.join(sorted(offered)) or 'nothing'}."
        )


def requirements(cls: type) -> list[str]:
    """The names `cls.__init__` asks for, in order, excluding self and
    anything with a default — a parameter that can go without is not a
    dependency, it is an option."""
    parameters = inspect.signature(cls.__init__).parameters.values()
    return [
        parameter.name
        for parameter in parameters
        if parameter.name != "self"
        and parameter.default is inspect.Parameter.empty
        and parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
    ]


def construct(cls: type, registry: dict[str, Any]):
    wanted = requirements(cls)
    missing = [name for name in wanted if name not in registry]
    if missing:
        raise MissingDependencies(cls, missing, list(registry))
    return cls(**{name: registry[name] for name in wanted})
