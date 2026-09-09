"""Which loader the engine will use, offered once and settled once.

The core has one loader it can always build — the Db/Archive-backed
AutomatonLoader — and no opinion about the others, because the others
live in packages a build may not contain: the platform's
compiled-or-interpreted loader, and a product's single-package one. So
main.py builds the default, publishes this on bus.POINT_AUTOMATON_LOADER,
and uses whatever comes back.

`replace` refuses a second replacement rather than letting the last
contributor win by accident. Two packages both claiming the loader is a
build that should not have been made — an editor and a compiled product
in the same backend — and a message naming both is worth more than a
silent choice nobody can trace afterwards.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AutomatonLoaderChoice:
    """`db`, `session_manager` and `apps_dir` are what a replacement is
    likely to need to build itself; `loader` is what the engine will
    actually get."""

    db: Any
    session_manager: Any
    apps_dir: Path
    loader: Any
    chosen_by: str = "core"

    def replace(self, loader: Any, by: str) -> None:
        if self.chosen_by != "core":
            raise RuntimeError(
                f"Both '{self.chosen_by}' and '{by}' claim the automaton loader — "
                f"this backend was built with two packages that cannot coexist."
            )
        self.loader = loader
        self.chosen_by = by
