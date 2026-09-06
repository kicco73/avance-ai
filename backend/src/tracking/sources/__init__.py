"""The `source` namespace a trigger/`env:` expression resolves against —
"data sources" a project's own `sources:` section declares by name, each
bound (via its own `url:` field, see tracking.sources.url) to a driver
whose every method is bounded by construction (tracking.sources.base.
SourceDriver — the `select_rows_*` reads and `update` today). `source.<name>` is resolved
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
from .comparison import OPERATORS
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

_STRINGS_PARAMETER = {
    "type": "array", "items": {"type": "string"},
    "description": (
        "Additional row filter, same semantics as select_rows_containing: only rows containing every one "
        "of these strings (case-insensitive substring match) are returned. Optional — omit or leave empty "
        "for no additional filter."
    ),
}

# The uniform JSON Schema of each SourceDriver method's own arguments —
# the same for every driver, since every driver implements the very same
# signature (see SourceDriver). A driver may *narrow* one of these
# through parameter_schema() (an enum of real column names, the exact
# writable fields), never change its shape.
METHOD_SCHEMAS: dict[str, dict] = {
    "select_rows_containing": {
        "type": "object",
        "properties": {"values": _VALUES_PARAMETER},
        "required": ["values"],
    },
    "select_rows_where": {
        "type": "object",
        "properties": {
            "column": {"type": "string", "description": "The column the comparison applies to."},
            "operator": {
                "type": "string", "enum": list(OPERATORS),
                "description": "How the column's value is compared to `value`.",
            },
            "value": {
                "type": "string",
                "description": (
                    "The value to compare against — a number or an ISO date (YYYY-MM-DD) where the column "
                    "holds one, plain text otherwise."
                ),
            },
            "strings": _STRINGS_PARAMETER,
        },
        "required": ["column", "operator", "value"],
    },
    "select_rows_in_range": {
        "type": "object",
        "properties": {
            "column": {"type": "string", "description": "The column the range applies to."},
            "start": {"type": "string", "description": "Lower bound, included — a number or an ISO date (YYYY-MM-DD)."},
            "end": {"type": "string", "description": "Upper bound, included — a number or an ISO date (YYYY-MM-DD)."},
            "strings": _STRINGS_PARAMETER,
        },
        "required": ["column", "start", "end"],
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

# Which SourceDriver method each of a state's three source fields exposes
# — a read field exposes every one of READ_METHODS the driver supports
# (READ_METHOD, the plain row search, is the one every readable driver
# implements, and so what a read field is validated against).
READ_METHODS = ("select_rows_containing", "select_rows_where", "select_rows_in_range")
READ_METHOD = READ_METHODS[0]
WRITE_METHOD = "update"

# method -> its own named arguments that precede that method's trailing
# `*strings` variadic (see SourceDriver.select_rows_where/
# select_rows_in_range) — every other method's variadic (`values`) comes
# first in its own signature, with no fixed arguments ahead of it (see
# ToolSet.call).
_FIXED_PARAMS: dict[str, tuple[str, ...]] = {
    "select_rows_where": ("column", "operator", "value"),
    "select_rows_in_range": ("column", "start", "end"),
}


class ToolSet:
    """The model's own callable catalog for one turn — one tool per
    READ_METHODS method its driver supports for every source a state
    names in ai-may-read-sources/ai-must-read-sources,
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
        # tool name -> its own source, for tool_event() below.
        self._sources: dict[str, Source] = {}
        self._specs: list[ToolSpec] = []
        # Every ai-must-read-sources read tool name — the subset
        # AiService forces tool_choice down to on the first round after
        # entering this state (see required_specs() below). Disjoint from
        # every ai-may-read-sources tool name by construction:
        # AutomatonBuilder rejects a source declared in both read fields
        # for the same state. An `update` is never in here: a write is
        # never forced.
        self._required_names: set[str] = set()
        for source in may_read:
            self._add_read_tools(source, required=False)
        for source in must_read:
            self._add_read_tools(source, required=True)
        for source in may_write or []:
            driver = self._namespace.driver_for(source)
            if WRITE_METHOD not in driver.SUPPORTED_METHODS:
                raise ValueError(f"source.{source.name}.{WRITE_METHOD}(...): not supported by this source.")
            self._add_tool(source, driver, WRITE_METHOD, required=False)

    def _add_read_tools(self, source: Source, *, required: bool) -> None:
        driver = self._namespace.driver_for(source)
        supported = [method for method in READ_METHODS if method in driver.SUPPORTED_METHODS]
        if not supported:
            raise ValueError(f"source.{source.name}.{READ_METHOD}(...): not supported by this source.")
        for method in supported:
            self._add_tool(source, driver, method, required=required)

    def _add_tool(self, source: Source, driver: SourceDriver, method: str, *, required: bool) -> None:
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
        self._sources[tool_name] = source
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
        """The ai-must-read-sources read subset of specs() — what
        AiService restricts tool_choice to on the first tool-call round
        after this state was entered (see TrackingProcessor.
        force_required_tools_for). Empty when this state declares no
        ai-must-read-sources at all; never contains an `update`."""
        return [spec for spec in self._specs if spec.name in self._required_names]

    def tool_event(self, name: str, arguments: dict, phase: str, **result_fields) -> dict:
        """The one place source/method/label/description are folded onto a
        tool call for `on_metadata("tool", ...)` — AiService's own loop
        composes nothing, it just forwards `round` (and, on phase
        "result", `result`/`duration_ms`) through `result_fields` and
        passes this straight to on_metadata. `rows`/`error` are derived
        here from `result` on phase "result" only — 0 rows on an empty or
        refused ("error:"-prefixed) result, every line after the header
        otherwise; a driver's own tabular-text convention, not a hard
        contract. Falls back to `name` for label and to no description on
        an unresolved tool name (defensive only: every name AiService's
        loop passes here came straight out of specs())."""
        source_name, method = self._resolved.get(name, (name, None))
        source = self._sources.get(name)
        payload = {
            "phase": phase,
            "name": name,
            "source": source_name,
            "method": method,
            "label": source.ui_label if source else name,
            "description": source.ui_description if source else None,
            "arguments": arguments,
            "round": result_fields.get("round"),
        }
        if phase == "result":
            result = result_fields.get("result", "")
            error = result.startswith("error:")
            payload.update({
                "result": result,
                "rows": 0 if error else max(0, len(result.splitlines()) - 1),
                "error": error,
                "duration_ms": result_fields.get("duration_ms"),
            })
        return payload

    async def call(self, name: str, arguments: dict) -> str:
        """Never raises — an unknown tool name, a bad argument, or the
        driver's own exception all come back as "error: <message>" so the
        model sees what went wrong and decides how to continue, the same
        as any other tool result. This is the exact text persisted to
        Tracking.tool_calls and shown, raw, in the chat UI's own
        expandable tool-call trace (see MessageBubble.vue) — never
        decorated with anything meant for the model alone (see AiService's
        own tool-call loop for that). The driver call itself is
        synchronous (disk/DB I/O) — always off the event loop via
        asyncio.to_thread, never blocking it. `values`/`strings` are every
        method's own variadic parameter (see SourceDriver), unpacked
        positionally, after any of _FIXED_PARAMS' own named arguments that
        precede it in the driver's own signature (select_rows_where/
        select_rows_in_range's `column`/`operator`/`value`/`start`/`end`) —
        those must be passed positionally too, since Python rejects a
        keyword argument for a parameter a positional *args also reaches.
        Every other argument is passed through by keyword. A write
        (`method == WRITE_METHOD`) also gets `origin="tool"` injected here,
        in Python, never through `arguments` — origin is never part of any
        tool's own JSON schema, so the model can neither see nor spoof it
        (see AvanceEnvSource.update's own `origin` docstring)."""
        resolved = self._resolved.get(name)
        if resolved is None:
            return f"error: unknown tool '{name}'."
        source_name, method = resolved
        try:
            driver = getattr(self._namespace, source_name)
            bound_method = getattr(driver, method)
            fixed = _FIXED_PARAMS.get(method, ())
            positional = [arguments[key] for key in fixed]
            variadic_name = "strings" if fixed else "values"
            variadic = arguments.get(variadic_name) or []
            keywords = {
                key: value for key, value in arguments.items() if key not in fixed and key != variadic_name
            }
            if method == WRITE_METHOD:
                keywords["origin"] = "tool"
            return await asyncio.to_thread(bound_method, *positional, *variadic, **keywords)
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
