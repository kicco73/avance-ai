"""The `avance` source driver — read-only access to one of a project's
own stored archive files, addressed by a `sources:` entry's own
`url: avance:<archive path>` (e.g. `avance:sources/flights.csv`).
`select_rows_containing(*values)`: the header row plus every whole row
containing *every* value (case-insensitive, AND'd — one value narrows
down to a single row, several narrow further; no values at all means
every row), bounded (see SourceDriver._bounded) regardless of how big a
match set it finds. `select_rows_where(column, operator, value, *strings)`
and `select_rows_in_range(column, start, end, *strings)`: the same
whole rows, picked by a comparison on one column instead (numbers and
ISO dates included, see tracking.sources.comparison), further narrowed by
`*strings` with the very same AND'd substring semantics as
`select_rows_containing`. No row at all
matching the filter returns "" — not even the header — so
`select_rows_containing(...) != ''` is a real existence check.
`value(*values, key=...)`: the `key` cell of the first matching row, numeric when the cell
reads as one (see tracking.sources.comparison.numeric_or_text) or the raw string otherwise, for
scripts/triggers that want one value rather than a table to parse —
never a model tool. `column(column, *values)`: every `column` cell of
the matching rows, as a list, for a script that wants the whole column
to look into — never a model tool either. `row_where(column, operator,
value, *strings)`: the first row `select_rows_where` would return, as a
`{column: cell}` dict, for a script that wants one whole record without
parsing a table — never a model tool either: TOOL_METHODS is the driver's
own say on which of its methods the model gets. A whole-file read is
`attachment.<doc_id>.read()`'s job (on-exit/task only, see
tracking.actuators.attachment_namespace) — SourceDriver itself has no
such method at all: every method here must return a bounded result, and
a whole file is exactly what bounding a result doesn't make sense for.

Where the bytes come from is not this driver's business: it asks the
ProjectFiles it was handed (see tracking.project_files), which is the
database at this automaton's pinned revision, or, for an automaton that
carries its own files, the package's own data/ directory — the real
project files, read directly, every time."""
from __future__ import annotations

import csv

from system.logging_factory import LoggerFactory

from .base import MAX_SOURCE_RESULT_CHARS, SourceContext, SourceDriver
from .comparison import OPERATORS, ColumnComparison, ColumnRange, numeric_or_text

logger = LoggerFactory.get_logger(__name__)

SCHEME = "avance"

_DELIMITERS = ",;\t|"


class AvanceArchiveSource(SourceDriver):
    SUPPORTED_METHODS = frozenset({
        "select_rows_containing", "select_rows_where", "select_rows_in_range", "value", "column", "select_subtable", "row_where",
    })
    TOOL_METHODS = ("select_rows_containing", "select_rows_where", "select_rows_in_range")
    METHOD_DESCRIPTIONS = {
        "select_rows_containing": (
            "Return rows containing ALL specified strings anywhere in the row. Case-insensitive substring "
            "matching. The header row (naming the columns) comes first; omit `values` for every row; \"\" "
            "means no row matched at all — e.g. source.<name>.select_rows_containing('Paris')."
        ),
        "select_rows_where": (
            "Return rows where a column satisfies a comparison. Operators: =, !=, >, >=, <, <=. Supports "
            "numeric values and ISO dates (YYYY-MM-DD). Additional strings, if provided, must also occur "
            "anywhere in the row — e.g. "
            "source.<name>.select_rows_where('data_partenza', '>=', '2026-08-16', 'Barcelona')."
        ),
        "select_rows_in_range": (
            "Return rows where a numeric or ISO date (YYYY-MM-DD) column is between `start` and `end`, "
            "inclusive. Additional strings, if provided, must also occur anywhere in the row — e.g. "
            "source.<name>.select_rows_in_range('data_partenza', '2026-08-01', '2026-08-31', 'Barcelona')."
        ),
        "value": (
            "The `key` column of the first row matching *every* given value, case-insensitive, as a "
            "single scalar — e.g. source.<name>.value('VY3003', key='data_partenza'). \"\" if no row "
            "matches. Scripts/triggers only, never a model tool — the model reads with the select_rows_* tools."
        ),
        "column": (
            "Every `column` cell of the rows matching *every* given value, case-insensitive, as a list — "
            "e.g. source.<name>.column('codice_volo', 'Barcelona'); no values means the whole column. "
            "[] if no row matches or the column doesn't exist. Scripts/triggers only, never a model tool."
        ),
        "select_subtable": (
            "The named columns, as a dict {column: [cells]} in the order asked — "
            "e.g. source.<name>.select_subtable('caso', 'titulo') gives {'caso': [1, 2], 'titulo': ['Ana', 'Luis']}. "
            "{} if there is no row at all, a column doesn't exist, or the result exceeds the size bound. What chat.write_table takes. "
            "Scripts/triggers only, never a model tool."
        ),
        "row_where": (
            "The first row where a column satisfies a comparison (same operators and `*strings` as "
            "select_rows_where), as a {column: cell} dict — e.g. source.<name>.row_where('caso', '=', 1). "
            "{} if no row matches, if the column or operator is unknown, or if the row exceeds the size bound. "
            "Scripts/triggers only, never a model tool."
        ),
    }

    def __init__(self, context: SourceContext, name: str, archive_path: str) -> None:
        super().__init__(context, name, archive_path)
        self._automaton = context.automaton
        self._archive_path = archive_path
        self._files = context.files

    def _read_text(self) -> str:
        found = self._files.read(self._archive_path)
        if found is None:
            raise ValueError(
                f"source.{self._name}: '{self._archive_path}' not found in project '{self._automaton.project_id}'."
            )
        content, media_type = found
        if not media_type.startswith("text/"):
            raise ValueError(
                f"source.{self._name}: '{self._archive_path}' is a binary file ({media_type}) — "
                "only text files can be read this way."
            )
        return content.decode("utf-8")

    @staticmethod
    def _delimiter(header: str) -> str:
        """The column separator this file actually uses, sniffed off its
        header row alone — comma unless another candidate clearly wins."""
        try:
            return csv.Sniffer().sniff(header, delimiters=_DELIMITERS).delimiter
        except csv.Error:
            return ","

    def _records(self) -> tuple[str, str, list[str], list[tuple[str, list[str]]]] | None:
        lines = self._read_text().splitlines(keepends=True)
        if not lines:
            return None
        delimiter = self._delimiter(lines[0])
        reader = csv.reader(lines, delimiter=delimiter)
        records: list[tuple[str, list[str]]] = []
        start = 0
        for cells in reader:
            records.append(("".join(lines[start:reader.line_num]), cells))
            start = reader.line_num
        header_text, header_cells = records[0]
        return header_text, delimiter, [name.strip() for name in header_cells], records[1:]

    def _matches(self, values: tuple[str | float, ...]) -> tuple[str, str, list[str], list[tuple[str, list[str]]]] | None:
        found = self._records()
        if found is None:
            return None
        header_text, delimiter, names, records = found
        needles = [str(value).lower() for value in values]
        matches = [record for record in records if all(needle in record[0].lower() for needle in needles)]
        return header_text, delimiter, names, matches

    @staticmethod
    def _unknown_column(column: str, names: list[str]) -> str:
        return f"error: unknown column(s) {column!r} — available: {', '.join(names)}"

    @staticmethod
    def _cell(cells: list[str], index: int) -> str | int | float:
        return numeric_or_text(cells[index]) if index < len(cells) else ""

    def select_rows_containing(self, *values: str | float) -> str:
        found = self._matches(values)
        if found is None:
            return ""
        header_text, _, _, matches = found
        if not matches:
            return ""
        return self._bounded(header_text + "".join(text for text, _ in matches), header=header_text)

    def select_rows_where(self, column: str, operator: str, value: str | float, *strings: str | float) -> str:
        if operator not in OPERATORS:
            return f"error: unknown operator {operator!r} — available: {', '.join(OPERATORS)}"
        return self._rows_where_column(column, ColumnComparison(operator, value), strings)

    def select_rows_in_range(self, column: str, start: str | float, end: str | float, *strings: str | float) -> str:
        return self._rows_where_column(column, ColumnRange(start, end), strings)

    def _rows_where_column(
        self, column: str, condition: ColumnComparison | ColumnRange, strings: tuple[str | float, ...] = (),
    ) -> str:
        found = self._matches(strings)
        if found is None:
            return ""
        header_text, _, names, records = found
        if column not in names:
            return self._unknown_column(column, names)
        matches = [text for text, _ in self._records_where_column(names, records, column, condition)]
        if not matches:
            return ""
        return self._bounded(header_text + "".join(matches), header=header_text)

    @staticmethod
    def _records_where_column(
        names: list[str], records: list[tuple[str, list[str]]], column: str, condition: ColumnComparison | ColumnRange,
    ) -> list[tuple[str, list[str]]]:
        index = names.index(column)
        return [(text, cells) for text, cells in records if index < len(cells) and condition.matches(cells[index])]

    def row_where(
        self, column: str, operator: str, value: str | float, *strings: str | float,
    ) -> dict[str, str | int | float]:
        if operator not in OPERATORS:
            logger.warning("source.%s.row_where: unknown operator %r — available: %s", self._name, operator, ", ".join(OPERATORS))
            return {}
        found = self._matches(strings)
        if found is None:
            return {}
        _, _, names, records = found
        if column not in names:
            logger.warning("source.%s.row_where(%r): unknown column — available: %s", self._name, column, ", ".join(names))
            return {}
        matches = self._records_where_column(names, records, column, ColumnComparison(operator, value))
        if not matches:
            return {}
        text, cells = matches[0]
        if len(text) > MAX_SOURCE_RESULT_CHARS:
            logger.warning("source.%s.row_where(%r): row over %d chars", self._name, column, MAX_SOURCE_RESULT_CHARS)
            return {}
        return {name: self._cell(cells, index) for index, name in enumerate(names)}

    def column(self, column: str, *values: str | float) -> list[str | int | float]:
        found = self._matches(values)
        if found is None:
            return []
        _, delimiter, names, matches = found
        if column not in names:
            logger.warning("source.%s.column(%r): unknown column — available: %s", self._name, column, ", ".join(names))
            return []
        index = names.index(column)
        values_found = [self._cell(cells, index) for _, cells in matches]
        if len(delimiter.join(str(v) for v in values_found)) > MAX_SOURCE_RESULT_CHARS:
            logger.warning("source.%s.column(%r): result over %d chars — narrow it with values", self._name, column, MAX_SOURCE_RESULT_CHARS)
            return []
        return values_found

    def select_subtable(self, *columns: str) -> dict[str, list[str | int | float]]:
        found = self._matches(())
        if found is None:
            return {}
        _, delimiter, names, records = found
        if not records:
            return {}
        unknown = [column for column in columns if column not in names]
        if unknown:
            logger.warning("source.%s.select_subtable(%r): unknown column(s) — available: %s", self._name, unknown, ", ".join(names))
            return {}
        table = {column: [self._cell(cells, names.index(column)) for _, cells in records] for column in columns}
        size = sum(len(delimiter.join(str(v) for v in [column, *values])) + 1 for column, values in table.items())
        if size > MAX_SOURCE_RESULT_CHARS:
            logger.warning("source.%s.select_subtable(%r): result over %d chars", self._name, columns, MAX_SOURCE_RESULT_CHARS)
            return {}
        return table

    def value(self, *values: str | float, key: str) -> str | int | float:
        found = self._matches(values)
        if found is None:
            return ""
        _, _, names, matches = found
        if not matches:
            return ""
        if key not in names:
            return self._unknown_column(key, names)
        index = names.index(key)
        return self._cell(matches[0][1], index)
