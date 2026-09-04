"""The uniform interface every source driver implements — what a
project's own `sources:` declaration binds a name to (see
tracking.sources.SourceNamespace). A driver overrides only the
operations it actually supports; every other one keeps this base's
default, which reports itself unsupported rather than silently no-op'ing.

Bounded by construction: `select` (and any future method a driver adds)
must return a result at most MAX_SOURCE_RESULT_CHARS long, truncated
here — once, for every caller alike (a trigger/env: expression, or the
model itself via ToolSet.call), never left to each driver to remember."""
from __future__ import annotations

# A driver's own raw result can be arbitrarily large (a multi-megabyte
# archive); nothing downstream — a trigger expression, an env: write, a
# tool result fed back to the model — should ever see more than this
# many characters of it. Not in config: a structural limit, not a
# per-deployment tuning knob.
MAX_SOURCE_RESULT_CHARS = 8_000


class SourceDriver:
    # Every method name this driver actually implements meaningfully —
    # what AutomatonBuilder validates a `source.<name>.<method>(...)`
    # reference against (see trigger_expression_analyzer.source_refs).
    SUPPORTED_METHODS: frozenset[str] = frozenset()

    # {method: description} for exactly those same methods — backs the
    # design view's own autocomplete (see project.inspector.
    # ProjectInspector.get_identifier_registry), same role
    # IdentifierRegistry's own fixed namespace dicts play elsewhere.
    METHOD_DESCRIPTIONS: dict[str, str] = {}

    def __init__(self, name: str) -> None:
        self._name = name

    def _unsupported(self, method: str) -> ValueError:
        return ValueError(f"source.{self._name}.{method}(...): not supported by this source.")

    @staticmethod
    def _bounded(text: str) -> str:
        """The one seam every result-returning method (today, just
        select()) routes through — a single place bounding a driver's own
        output, regardless of who's asking."""
        if len(text) <= MAX_SOURCE_RESULT_CHARS:
            return text
        remaining = len(text) - MAX_SOURCE_RESULT_CHARS
        return f"{text[:MAX_SOURCE_RESULT_CHARS]}\n[truncated: {remaining} more characters]"

    def select(self, value: str) -> str:
        raise self._unsupported("select")
