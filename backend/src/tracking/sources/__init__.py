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

import asyncio
import inspect
from typing import Any, Sequence

from automaton.automaton import Automaton, Source
from db import Db
from ai import ToolSpec

from .avance_archive import SCHEME as AVANCE_ARCHIVE_SCHEME, AvanceArchiveSource
from .base import SourceDriver
from .url import parse_source_url

SOURCE_DRIVERS: dict[str, type[SourceDriver]] = {
    AVANCE_ARCHIVE_SCHEME: AvanceArchiveSource,
}


def _method_parameters(method: Any) -> dict:
    """JSON Schema (object) for `method`'s own arguments — every one a
    plain string, all required (ToolSpec's own contract; every
    SourceDriver method takes only string arguments today)."""
    properties = {
        name: {"type": "string"}
        for name in inspect.signature(method).parameters
        if name != "self"
    }
    return {"type": "object", "properties": properties, "required": list(properties.keys())}


class ToolSet:
    """The model's own callable catalog for one turn — every
    SourceDriver method of every source named in a state's own `tools:`
    (see automaton.State.tools), resolved through the same
    SourceNamespace instance (and so the same per-session read cache) a
    source.<name>.<method>() expression already uses. Same shape as
    ActuatorSet — a catalog plus per-session execution — but never its
    mechanics: call() below runs inline, inside AiService's own tool-call
    loop, and the model waits on its result within that same request —
    never scheduled as an OnEnterTask/JobService job, never persisted the
    way an on-enter script is."""

    def __init__(self, namespace: "SourceNamespace", sources: list[Source]) -> None:
        self._namespace = namespace
        # tool name ("source_<name>_<method>") -> (source name, method name)
        self._resolved: dict[str, tuple[str, str]] = {}
        self._specs: list[ToolSpec] = []
        for source in sources:
            scheme, _path = parse_source_url(source.url)
            driver_cls = SOURCE_DRIVERS[scheme]
            for method in sorted(driver_cls.SUPPORTED_METHODS):
                tool_name = f"source_{source.name}_{method}"
                description = driver_cls.METHOD_DESCRIPTIONS.get(method, "")
                # ui-description is where the project author explains what
                # the file actually contains and how to search it — the
                # model needs that alongside the generic method blurb to
                # use the tool well.
                if source.ui_description:
                    description = f"{description}\n\n{source.ui_description}" if description else source.ui_description
                self._resolved[tool_name] = (source.name, method)
                self._specs.append(ToolSpec(
                    name=tool_name, description=description,
                    parameters=_method_parameters(getattr(driver_cls, method)),
                ))

    def specs(self) -> list[ToolSpec]:
        return list(self._specs)

    async def call(self, name: str, arguments: dict) -> str:
        """Never raises — an unknown tool name, a bad argument, or the
        driver's own exception all come back as "error: <message>" so the
        model sees what went wrong and decides how to continue, the same
        as any other tool result. The driver call itself is synchronous
        (disk/DB I/O) — always off the event loop via asyncio.to_thread,
        never blocking it."""
        resolved = self._resolved.get(name)
        if resolved is None:
            return f"error: unknown tool '{name}'."
        source_name, method = resolved
        try:
            driver = getattr(self._namespace, source_name)
            bound_method = getattr(driver, method)
            return await asyncio.to_thread(bound_method, **arguments)
        except Exception as exc:
            return f"error: {exc}"


class SourceNamespace:
    def __init__(self, db: Db, automaton: Automaton, session_id: int | None = None) -> None:
        self._db = db
        self._automaton = automaton
        # None outside a real chat session (see tracking.evaluation_scope.
        # EvaluationScopeBuilder.build) — passed straight through to every
        # driver so it can decide for itself whether/how to use it
        # (AvanceArchiveSource's own per-session read cache).
        self._session_id = session_id

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        source = next((s for s in self._automaton.sources if s.name == name), None)
        if source is None:
            raise ValueError(f"source.{name}: no such source declared in this project's own 'sources:' section.")
        scheme, path = parse_source_url(source.url)
        driver_cls = SOURCE_DRIVERS[scheme]
        return driver_cls(self._db, self._automaton, name, path, session_id=self._session_id)

    def tool_set(self, names: Sequence[str]) -> ToolSet:
        """The catalog for a state's own `tools:` list — resolved against
        this same automaton's declared sources (AutomatonBuilder already
        validated every name at build time, so an unresolvable one here
        would only ever mean a stale automaton snapshot, not a real
        config error)."""
        sources = []
        for name in names:
            source = next((s for s in self._automaton.sources if s.name == name), None)
            if source is None:
                raise ValueError(f"source.{name}: no such source declared in this project's own 'sources:' section.")
            sources.append(source)
        return ToolSet(self, sources)
