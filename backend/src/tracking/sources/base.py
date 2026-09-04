"""The uniform interface every source driver implements — what a
project's own `sources:` declaration binds a name to (see
tracking.sources.SourceNamespace). A driver overrides only the
operations it actually supports; every other one keeps this base's
default, which reports itself unsupported rather than silently no-op'ing."""
from __future__ import annotations


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

    def create(self, key: str, value: str) -> None:
        raise self._unsupported("create")

    def read(self) -> str:
        raise self._unsupported("read")

    def update(self, key: str, value: str) -> None:
        raise self._unsupported("update")

    def delete(self, key: str) -> None:
        raise self._unsupported("delete")

    def select(self, value: str) -> str:
        raise self._unsupported("select")
