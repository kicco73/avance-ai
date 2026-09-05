"""The `source` namespace a trigger/`env:` expression resolves against —
"data sources" a project's own `sources:` section declares by name, each
bound (via its own `url:` field, see tracking.sources.url) to a driver
whose every method is bounded by construction (tracking.sources.base.
SourceDriver — select is the only one today). `source.<name>` is
resolved dynamically, per project, against that declaration — nothing is
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


def _method_parameters(method: Any) -> tuple[dict, str | None]:
    """(JSON Schema for `method`'s own arguments, the name of its one
    variadic parameter if it has one). Every plain parameter is a
    required string (ToolSpec's own contract); a `*values`-style
    variadic parameter (only `SourceDriver.select` today, for cascading
    filters — see AvanceArchiveSource.select) instead becomes a required
    array of strings, and its name is returned separately since it can
    never be passed through as a plain `**arguments` keyword the way the
    others are (see ToolSet.call)."""
    properties: dict[str, dict] = {}
    variadic_param: str | None = None
    for name, parameter in inspect.signature(method).parameters.items():
        if name == "self":
            continue
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            properties[name] = {"type": "array", "items": {"type": "string"}, "minItems": 1}
            variadic_param = name
        else:
            properties[name] = {"type": "string"}
    return {"type": "object", "properties": properties, "required": list(properties.keys())}, variadic_param


class ToolSet:
    """The model's own callable catalog for one turn — every
    SourceDriver method of every source named in a state's own
    ai-may-query-sources/ai-must-query-sources (see automaton.State),
    resolved through the same SourceNamespace instance (and so the same
    per-session read cache) a source.<name>.<method>() expression already
    uses. Same shape as ActuatorSet — a catalog plus per-session
    execution — but never its mechanics: call() below runs inline, inside
    AiService's own tool-call loop, and the model waits on its result
    within that same request — never scheduled as an OnEnterTask/
    JobService job, never persisted the way an on-enter script is."""

    def __init__(self, namespace: "SourceNamespace", may_sources: list[Source], must_sources: list[Source]) -> None:
        self._namespace = namespace
        # tool name ("source_<name>_<method>") -> (source name, method name)
        self._resolved: dict[str, tuple[str, str]] = {}
        # tool name -> its own source's ui_label, for status_text()/
        # summary_text() below — kept separate from _resolved rather than
        # folded in: nothing else needs it, and a driver could one day
        # expose a method with no single "source" behind it (e.g.
        # cross-source), which would still need to resolve here but
        # wouldn't have one ui_label to report.
        self._ui_labels: dict[str, str] = {}
        # tool name -> the name of its one variadic parameter, if it has
        # one — see _method_parameters and call() below.
        self._variadic_params: dict[str, str | None] = {}
        self._specs: list[ToolSpec] = []
        # Every ai-must-query-sources tool name — the subset AiService
        # forces tool_choice down to on the first round after entering
        # this state (see required_specs() below). Disjoint from every
        # ai-may-query-sources tool name by construction: AutomatonBuilder
        # rejects a source declared in both fields for the same state.
        self._required_names: set[str] = set()
        for source in may_sources:
            self._add_source(source, required=False)
        for source in must_sources:
            self._add_source(source, required=True)

    def _add_source(self, source: Source, *, required: bool) -> None:
        scheme, _path = parse_source_url(source.url)
        driver_cls = SOURCE_DRIVERS[scheme]
        for method in sorted(driver_cls.SUPPORTED_METHODS):
            tool_name = f"source_{source.name}_{method}"
            description = driver_cls.METHOD_DESCRIPTIONS.get(method, "")
            # ai-definition is where the project author explains — *to the
            # model* — what the file actually contains and how to search
            # it, alongside the generic method blurb; never ui-description,
            # which is human-facing UI text that must never reach the model.
            if source.ai_definition:
                description = f"{description}\n\n{source.ai_definition}" if description else source.ai_definition
            parameters, variadic_param = _method_parameters(getattr(driver_cls, method))
            self._resolved[tool_name] = (source.name, method)
            self._ui_labels[tool_name] = source.ui_label
            self._variadic_params[tool_name] = variadic_param
            self._specs.append(ToolSpec(name=tool_name, description=description, parameters=parameters))
            if required:
                self._required_names.add(tool_name)

    @property
    def session_id(self) -> int | None:
        """Forwarded from this ToolSet's own SourceNamespace — for
        AiService's own per-tool-call log line (see its
        generate_stream_with_metadata) only; nothing else here needs it."""
        return self._namespace.session_id

    @property
    def project_id(self) -> str:
        """Forwarded from this ToolSet's own SourceNamespace — AiService
        has no other way to name the project a SystemWarning over budget
        belongs to (see its own _enforce_input_budget)."""
        return self._namespace.project_id

    def specs(self) -> list[ToolSpec]:
        return list(self._specs)

    def required_specs(self) -> list[ToolSpec]:
        """The ai-must-query-sources subset of specs() — what AiService
        restricts tool_choice to on the first tool-call round after this
        state was entered (see TrackingProcessor.force_required_tools_for).
        Empty when this state declares no ai-must-query-sources at all."""
        return [spec for spec in self._specs if spec.name in self._required_names]

    def status_text(self, name: str) -> str:
        """A short, human line describing what calling `name` is about to
        do — e.g. "Searching Flights…" — the backend-composed text
        AiService's own tool-call loop attaches to its 'tool_call' event
        for the frontend to show verbatim as a transient line while the
        call is in flight. Falls back to the raw tool name for one this
        ToolSet doesn't itself recognize (defensive only: every name
        AiService's loop passes here came straight out of specs())."""
        return f"Searching {self._ui_labels.get(name, name)}…"

    def summary_text(self, name: str, arguments: dict, result: str) -> str:
        """The permanent, compact line left under the assistant's message
        once a call completes — e.g. `Searched Flight records for
        "VY3003" · 57 rows` — sent alongside 'tool_result' as
        `summary_text` and, by riding along in that same dict, persisted
        into Tracking.tool_calls for free (see AiService's own tool-call
        loop and db.tracking.record_tool_calls) so reopening the session
        can render it again with no separate storage of its own. Row
        count: every line after the header, minus a trailing
        "[truncated: ...]" marker line if SourceDriver._bounded added one
        — a best-effort count against a driver's own tabular-text
        convention, not a hard contract."""
        ui_label = self._ui_labels.get(name, name)
        variadic_param = self._variadic_params.get(name)
        values = arguments.get(variadic_param, []) if variadic_param else list(arguments.values())
        query = ", ".join(f'"{value}"' for value in values)
        lines = result.splitlines()
        if lines and lines[-1].startswith("[truncated:"):
            lines = lines[:-1]
        rows = max(0, len(lines) - 1)
        row_word = "row" if rows == 1 else "rows"
        return f"Searched {ui_label} for {query} · {rows} {row_word}"

    async def call(self, name: str, arguments: dict) -> str:
        """Never raises — an unknown tool name, a bad argument, or the
        driver's own exception all come back as "error: <message>" so the
        model sees what went wrong and decides how to continue, the same
        as any other tool result. The driver call itself is synchronous
        (disk/DB I/O) — always off the event loop via asyncio.to_thread,
        never blocking it. A variadic parameter (see _method_parameters)
        must be unpacked as positional args — `**arguments` alone can
        never fill a `*values`-style parameter."""
        resolved = self._resolved.get(name)
        if resolved is None:
            return f"error: unknown tool '{name}'."
        source_name, method = resolved
        try:
            driver = getattr(self._namespace, source_name)
            bound_method = getattr(driver, method)
            variadic_param = self._variadic_params.get(name)
            if variadic_param is not None:
                return await asyncio.to_thread(bound_method, *arguments.get(variadic_param, []))
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

    @property
    def session_id(self) -> int | None:
        return self._session_id

    @property
    def project_id(self) -> str:
        return self._automaton.project_id

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        source = next((s for s in self._automaton.sources if s.name == name), None)
        if source is None:
            raise ValueError(f"source.{name}: no such source declared in this project's own 'sources:' section.")
        scheme, path = parse_source_url(source.url)
        driver_cls = SOURCE_DRIVERS[scheme]
        return driver_cls(self._db, self._automaton, name, path, session_id=self._session_id)

    def tool_set(self, may_names: Sequence[str], must_names: Sequence[str] = ()) -> ToolSet:
        """The catalog for a state's own ai-may-query-sources/
        ai-must-query-sources lists — resolved against this same
        automaton's declared sources (AutomatonBuilder already validated
        every name, and that the two lists are disjoint, at build time —
        so an unresolvable one here would only ever mean a stale automaton
        snapshot, not a real config error)."""
        def _resolve(names: Sequence[str]) -> list[Source]:
            resolved = []
            for name in names:
                source = next((s for s in self._automaton.sources if s.name == name), None)
                if source is None:
                    raise ValueError(f"source.{name}: no such source declared in this project's own 'sources:' section.")
                resolved.append(source)
            return resolved
        return ToolSet(self, _resolve(may_names), _resolve(must_names))
