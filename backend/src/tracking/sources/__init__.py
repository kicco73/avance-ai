"""The `source` namespace a trigger/`env:` expression resolves against —
"data sources" a project's own `sources:` section declares by name, each
bound (via its own `url:` field, see tracking.sources.url) to a driver
that implements the uniform create/read/update/delete/select interface
(tracking.sources.base.SourceDriver). `source.<name>` is resolved
dynamically, per project, against that declaration — nothing is
registered ahead of time, unlike the old fixed source.attachment/
source.search dispatch table this replaces. Adding a driver means adding
a module plus one SOURCE_DRIVERS entry below, never touching
tracking.evaluation_scope or SourceNamespace itself."""
from __future__ import annotations

from typing import Any

from automaton.automaton import Automaton
from db import Db

from .avance_archive import SCHEME as AVANCE_ARCHIVE_SCHEME, AvanceArchiveSource
from .base import SourceDriver
from .url import parse_source_url

SOURCE_DRIVERS: dict[str, type[SourceDriver]] = {
    AVANCE_ARCHIVE_SCHEME: AvanceArchiveSource,
}


class SourceNamespace:
    def __init__(self, db: Db, automaton: Automaton) -> None:
        self._db = db
        self._automaton = automaton

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        source = next((s for s in self._automaton.sources if s.name == name), None)
        if source is None:
            raise ValueError(f"source.{name}: no such source declared in this project's own 'sources:' section.")
        scheme, path = parse_source_url(source.url)
        driver_cls = SOURCE_DRIVERS[scheme]
        return driver_cls(self._db, self._automaton, name, path)
