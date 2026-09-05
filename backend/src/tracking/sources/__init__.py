"""The `source` namespace a trigger/`env:` expression resolves against —
"data sources" a project's own `sources:` section declares by name, each
bound (via its own `url:` field, see tracking.sources.url) to a driver
whose every method is bounded by construction (tracking.sources.base.
SourceDriver — `select` and `update` today). `source.<name>` is resolved
dynamically, per project, against that declaration — nothing is
registered ahead of time. Adding a driver means adding a module plus one
entry in driver_class_for below, never touching
tracking.evaluation_scope or SourceNamespace itself."""
from __future__ import annotations

import asyncio
from typing import Any, Sequence

from automaton.automaton import Automaton, Source
from db import Db
from ai import ToolSpec
from tracking.env import Env

from .avance_archive import SCHEME as AVANCE_SCHEME, AvanceArchiveSource
from .avance_env import PATH as AVANCE_ENV_PATH, AvanceEnvSource
from .base import SourceContext, SourceDriver
from .url import parse_source_url

# scheme -> the driver that serves it, except where a scheme's own path
# selects a different one (see driver_class_for): `avance:env` is the
# project's env keys, every other `avance:<path>` one of its archive files.
SOURCE_DRIVERS: dict[str, type[SourceDriver]] = {
    AVANCE_SCHEME: AvanceArchiveSource,
}


def driver_class_for(url: str) -> type[SourceDriver]:
    """The one place a source's `url` is turned into a driver class —
    raises ValueError for a malformed url and KeyError for an unknown
    scheme, exactly as parse_source_url/SOURCE_DRIVERS themselves do."""
    scheme, path = parse_source_url(url)
    if scheme == AVANCE_SCHEME and path == AVANCE_ENV_PATH:
        return AvanceEnvSource
    return SOURCE_DRIVERS[scheme]


_VALUES_PARAMETER = {
    "type": "array", "items": {"type": "string"},
    "description": (
        "Row filter: only rows containing every one of these values (case-insensitive) are affected. "
        "An empty list means every row."
    ),
}

# The uniform JSON Schema of each SourceDriver method's own arguments —
# the same for every driver, since every driver implements the very same
# signature (see SourceDriver). A driver may *narrow* one of these
# through parameter_schema() (an enum of real column names, the exact
# writable fields), never change its shape.
METHOD_SCHEMAS: dict[str, dict] = {
    "select": {
        "type": "object",
        "properties": {
            "values": _VALUES_PARAMETER,
            "keys": {
                "type": "array", "items": {"type": "string"},
                "description": "The columns to return, in this order; omit to return every column.",
            },
        },
        "required": ["values"],
    },
    "update": {
        "type": "object",
        "properties": {
            "values": _VALUES_PARAMETER,
            "fields": {
                "type": "object", "additionalProperties": {"type": "string"}, "minProperties": 1,
                "description": "Column name → new value, assigned to every matching row.",
            },
        },
        "required": ["values", "fields"],
    },
}

# Which SourceDriver method each of a state's three source fields exposes.
READ_METHOD = "select"
WRITE_METHOD = "update"


class ToolSet:
    """The model's own callable catalog for one turn — `select` for every
    source a state names in ai-may-read-sources/ai-must-read-sources,
    `update` for every one in ai-may-write-sources (see automaton.State),
    resolved through the same SourceNamespace instance (and so the same
    per-session read cache and the same Env) a source.<name>.<method>()
    expression already uses. Same shape as ActuatorSet — a catalog plus
    per-session execution — but never its mechanics: call() below runs
    inline, inside AiService's own tool-call loop, and the model waits on
    its result within that same request — never scheduled as an
    OnEnterTask/JobService job, never persisted the way an on-enter script is."""

    def __init__(
        self, namespace: "SourceNamespace", may_read: list[Source], must_read: list[Source],
        may_write: list[Source] | None = None,
    ) -> None:
        self._namespace = namespace
        # tool name ("source_<name>_<method>") -> (source name, method name)
        self._resolved: dict[str, tuple[str, str]] = {}
        # tool name -> its own source's ui_label, for status_text()/
        # summary_text() below.
        self._ui_labels: dict[str, str] = {}
        self._specs: list[ToolSpec] = []
        # Every ai-must-read-sources `select` tool name — the subset
        # AiService forces tool_choice down to on the first round after
        # entering this state (see required_specs() below). Disjoint from
        # every ai-may-read-sources tool name by construction:
        # AutomatonBuilder rejects a source declared in both read fields
        # for the same state. An `update` is never in here: a write is
        # never forced.
        self._required_names: set[str] = set()
        for source in may_read:
            self._add_tool(source, READ_METHOD, required=False)
        for source in must_read:
            self._add_tool(source, READ_METHOD, required=True)
        for source in may_write or []:
            self._add_tool(source, WRITE_METHOD, required=False)

    def _add_tool(self, source: Source, method: str, *, required: bool) -> None:
        driver = self._namespace.driver_for(source)
        if method not in driver.SUPPORTED_METHODS:
            raise ValueError(f"source.{source.name}.{method}(...): not supported by this source.")
        tool_name = f"source_{source.name}_{method}"
        description = driver.METHOD_DESCRIPTIONS.get(method, "")
        # ai-definition is where the project author explains — *to the
        # model* — what the source actually contains and how to use it,
        # alongside the generic method blurb; never ui-description, which
        # is human-facing UI text that must never reach the model.
        if source.ai_definition:
            description = f"{description}\n\n{source.ai_definition}" if description else source.ai_definition
        parameters = driver.parameter_schema(method) or METHOD_SCHEMAS[method]
        self._resolved[tool_name] = (source.name, method)
        self._ui_labels[tool_name] = source.ui_label
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
        """The ai-must-read-sources `select` subset of specs() — what
        AiService restricts tool_choice to on the first tool-call round
        after this state was entered (see TrackingProcessor.
        force_required_tools_for). Empty when this state declares no
        ai-must-read-sources at all; never contains an `update`."""
        return [spec for spec in self._specs if spec.name in self._required_names]

    def _method_of(self, name: str) -> str | None:
        resolved = self._resolved.get(name)
        return resolved[1] if resolved else None

    def status_text(self, name: str) -> str:
        """A short, human line describing what calling `name` is about to
        do — e.g. "Searching Flights…" / "Updating Env…" — the
        backend-composed text AiService's own tool-call loop attaches to
        its 'tool_call' event for the frontend to show verbatim as a
        transient line while the call is in flight. Falls back to the raw
        tool name for one this ToolSet doesn't itself recognize
        (defensive only: every name AiService's loop passes here came
        straight out of specs())."""
        verb = "Updating" if self._method_of(name) == WRITE_METHOD else "Searching"
        return f"{verb} {self._ui_labels.get(name, name)}…"

    def summary_text(self, name: str, arguments: dict, result: str) -> str:
        """The permanent, compact line left under the assistant's message
        once a call completes — `Searched Flight records for "VY3003" · 57
        rows` for a select, one `Set flight = "VY3003"` line per field for
        an update — sent alongside 'tool_result' as `summary_text` and, by
        riding along in that same dict, persisted into Tracking.tool_calls
        for free (see AiService's own tool-call loop and
        db.tracking.record_tool_calls) so reopening the session can render
        it again with no separate storage of its own. Row count: every
        line after the header, minus a trailing "[truncated: ...]" marker
        line if SourceDriver._bounded added one — a best-effort count
        against a driver's own tabular-text convention, not a hard contract."""
        ui_label = self._ui_labels.get(name, name)
        if self._method_of(name) == WRITE_METHOD:
            if result.startswith("error:"):
                return f"Could not update {ui_label}: {result[len('error:'):].strip()}"
            fields = arguments.get("fields") or {}
            return "\n".join(f'Set {key} = "{value}"' for key, value in fields.items()) or f"Updated {ui_label}"
        values = arguments.get("values") or []
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
        never blocking it. `values` is every method's own variadic
        parameter (see SourceDriver), unpacked positionally; every other
        argument is passed through by keyword."""
        resolved = self._resolved.get(name)
        if resolved is None:
            return f"error: unknown tool '{name}'."
        source_name, method = resolved
        try:
            driver = getattr(self._namespace, source_name)
            bound_method = getattr(driver, method)
            values = arguments.get("values") or []
            keywords = {key: value for key, value in arguments.items() if key != "values"}
            return await asyncio.to_thread(bound_method, *values, **keywords)
        except Exception as exc:
            return f"error: {exc}"


class SourceNamespace:
    def __init__(self, db: Db | None, automaton: Automaton, session_id: int | None = None, env: Env | None = None) -> None:
        # None outside a real chat session (see tracking.evaluation_scope.
        # EvaluationScopeBuilder.build) — passed straight through to every
        # driver so it can decide for itself whether/how to use it
        # (AvanceArchiveSource's own per-session read cache). `env`: the
        # session's own Env, what an avance:env source reads and writes —
        # a throwaway in-memory one when the caller has none (a namespace
        # built only to resolve archive sources).
        self._context = SourceContext(db=db, automaton=automaton, session_id=session_id, env=env if env is not None else Env())

    @property
    def session_id(self) -> int | None:
        return self._context.session_id

    @property
    def project_id(self) -> str:
        return self._context.automaton.project_id

    def _declared(self, name: str) -> Source:
        source = next((s for s in self._context.automaton.sources if s.name == name), None)
        if source is None:
            raise ValueError(f"source.{name}: no such source declared in this project's own 'sources:' section.")
        return source

    def driver_for(self, source: Source) -> SourceDriver:
        _scheme, path = parse_source_url(source.url)
        return driver_class_for(source.url)(self._context, source.name, path)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return self.driver_for(self._declared(name))

    def tool_set(
        self, may_read_names: Sequence[str], must_read_names: Sequence[str] = (), may_write_names: Sequence[str] = (),
    ) -> ToolSet:
        """The catalog for a state's own ai-may-read-sources/
        ai-must-read-sources/ai-may-write-sources lists — resolved against
        this same automaton's declared sources (AutomatonBuilder already
        validated every name, that the two read lists are disjoint, and
        that every write source's driver supports update, at build time —
        so an unresolvable one here would only ever mean a stale automaton
        snapshot, not a real config error)."""
        return ToolSet(
            self, [self._declared(name) for name in may_read_names], [self._declared(name) for name in must_read_names],
            [self._declared(name) for name in may_write_names],
        )
