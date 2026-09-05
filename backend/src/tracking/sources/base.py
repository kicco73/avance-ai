"""The uniform interface every source driver implements — what a
project's own `sources:` declaration binds a name to (see
tracking.sources.SourceNamespace). A driver overrides only the
operations it actually supports; every other one keeps this base's
default, which reports itself unsupported rather than silently no-op'ing.
There is deliberately no hierarchy of driver kinds: SUPPORTED_METHODS/
METHOD_DESCRIPTIONS per class are the whole compatibility mechanism, and
parameter_schema() only ever *narrows* a method's uniform argument schema
(see tracking.sources.METHOD_SCHEMAS) — never changes its signature.

Bounded by construction: `select` (and any future result-returning
method a driver adds) must return a result at most MAX_SOURCE_RESULT_CHARS
long, truncated here — once, for every caller alike (a trigger/env:
expression, or the model itself via ToolSet.call), never left to each
driver to remember."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automaton.automaton import Automaton
    from db import Db
    from tracking.env import Env

# A driver's own raw result can be arbitrarily large (a multi-megabyte
# archive); nothing downstream — a trigger expression, an env: write, a
# tool result fed back to the model — should ever see more than this
# many characters of it. Not in config: a structural limit, not a
# per-deployment tuning knob.
MAX_SOURCE_RESULT_CHARS = 8_000


@dataclass(frozen=True)
class SourceContext:
    """Everything a driver may need from the world it runs in, handed to
    every driver alike by SourceNamespace — a driver picks what it uses
    (AvanceArchiveSource reads `db` at `automaton`'s pinned revision,
    through a per-`session_id` cache; AvanceEnvSource reads and writes
    `env`). `session_id` is None outside a real chat session (a wake-up
    re-evaluation, a test replay, an on-enter deferred call)."""
    db: "Db | None"
    automaton: "Automaton"
    session_id: int | None
    env: "Env"


class SourceDriver:
    # Every method name this driver actually implements meaningfully —
    # what AutomatonBuilder validates a `source.<name>.<method>(...)`
    # reference against (see trigger_expression_analyzer.source_refs),
    # and what ToolSet exposes to the model for a source a state lists.
    SUPPORTED_METHODS: frozenset[str] = frozenset()

    # {method: description} for exactly those same methods — backs the
    # design view's own autocomplete (see project.inspector.
    # ProjectInspector.get_identifier_registry), same role
    # IdentifierRegistry's own fixed namespace dicts play elsewhere, and
    # the generic half of each tool's own description (see ToolSet).
    METHOD_DESCRIPTIONS: dict[str, str] = {}

    def __init__(self, context: SourceContext, name: str, path: str) -> None:
        self._context = context
        self._name = name
        self._path = path

    def _unsupported(self, method: str) -> ValueError:
        return ValueError(f"source.{self._name}.{method}(...): not supported by this source.")

    @staticmethod
    def _bounded(text: str) -> str:
        """The one seam every result-returning method routes through — a
        single place bounding a driver's own output, regardless of who's asking."""
        if len(text) <= MAX_SOURCE_RESULT_CHARS:
            return text
        remaining = len(text) - MAX_SOURCE_RESULT_CHARS
        return f"{text[:MAX_SOURCE_RESULT_CHARS]}\n[truncated: {remaining} more characters]"

    def select(self, *values: str, keys: list[str] | None = None) -> str:
        """Header row plus every row containing *every* value (case-
        insensitive substring, AND'd), then projected onto `keys` — the
        header always included, columns in the order asked for; None
        means every column. An unknown column comes back as an error
        *text* (the model reads it and retries), never an exception."""
        raise self._unsupported("select")

    def update(self, *values: str, fields: dict[str, str]) -> str:
        """Assigns `fields` (column -> new value) to every row containing
        *every* value, and reports how many rows it touched ("1 row
        updated"). Unsupported by default — a driver that can't write
        simply never declares it in SUPPORTED_METHODS."""
        raise self._unsupported("update")

    def parameter_schema(self, method: str) -> dict | None:
        """A driver-specific *narrowing* of `method`'s uniform JSON Schema
        (see tracking.sources.METHOD_SCHEMAS) — e.g. an enum of the
        columns that actually exist — or None (the default) to keep the
        generic one. Must describe the very same arguments the method
        takes: this only ever constrains values, never changes a signature."""
        return None
