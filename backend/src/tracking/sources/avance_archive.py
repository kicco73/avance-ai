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
`value(*values, key=...)`: the `key` cell of the first matching row, as a scalar string, for
scripts/triggers that want one value rather than a table to parse —
never a model tool. A whole-file read is
`attachment.read(name)`'s job (on-enter only, see
tracking.actuators.attachment_namespace) — SourceDriver itself has no
such method at all: every method here must return a bounded result, and
a whole file is exactly what bounding a result doesn't make sense for.

Every read goes through a per-chat-session cache copy under
`{CACHE_DIR}/sessions/<session id>/<archive path>` rather than the
canonical archive directly — see _read_text's own docstring for why."""
from __future__ import annotations

import csv
import io

from project.archive.layout import CACHE_DIR

from .base import SourceContext, SourceDriver
from .comparison import OPERATORS, ColumnComparison, ColumnRange

SCHEME = "avance"

_DELIMITERS = ",;\t|"


class AvanceArchiveSource(SourceDriver):
    SUPPORTED_METHODS = frozenset({
        "select_rows_containing", "select_rows_where", "select_rows_in_range", "value",
    })
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
    }

    def __init__(self, context: SourceContext, name: str, archive_path: str) -> None:
        super().__init__(context, name, archive_path)
        assert context.db is not None
        self._db = context.db
        self._automaton = context.automaton
        self._archive_path = archive_path
        # None outside a real chat session (a wake-up re-evaluation, a
        # test replay, an on-enter deferred call with no session recorded)
        # — _read_text falls back to the canonical archive directly then,
        # since there's no session of its own for a cache copy to belong to.
        self._session_id = context.session_id

    def _cache_archive_path(self) -> str:
        return f"{CACHE_DIR}/sessions/{self._session_id}/{self._archive_path}"

    def _read_canonical(self) -> tuple[str, str]:
        """(content, content_type) straight off the canonical archive, at
        this automaton's own pinned revision — never Db's own "current"
        default, wrong for a session pinned to an older one."""
        content_type = self._db.get_archive_content_type(
            self._automaton.project_id, self._archive_path, revision=self._automaton.revision,
        )
        if content_type is None:
            raise ValueError(
                f"source.{self._name}: '{self._archive_path}' not found in project '{self._automaton.project_id}'."
            )
        if not content_type.startswith("text/"):
            raise ValueError(
                f"source.{self._name}: '{self._archive_path}' is a binary file ({content_type}) — "
                "only text files can be read this way."
            )
        content = self._db.get_archive(self._automaton.project_id, self._archive_path, revision=self._automaton.revision)
        assert content is not None  # same Archive row get_archive_content_type just found this content_type on
        return content.decode("utf-8"), content_type

    def _read_text(self) -> str:
        """Reads through this session's own cache copy of the archive
        (see _cache_archive_path), duplicating it from the canonical one
        on a miss — first read of a session (only) pays for a second Db
        round trip; a session-scoped copy also means the rest of a
        conversation keeps seeing the same content even if the project
        gets edited/republished underneath it mid-session."""
        if self._automaton.revision is None:
            raise ValueError(f"source.{self._name}: this automaton has no known storage location to read from.")
        # project_id is always set by the time revision is (see
        # Automaton.set_storage_location's own docstring) — revision alone
        # is the "no known storage location" signal checked above.
        assert self._automaton.project_id is not None
        if self._session_id is None:
            content, _ = self._read_canonical()
            return content
        cache_path = self._cache_archive_path()
        cached = self._db.get_archive(self._automaton.project_id, cache_path, revision=self._automaton.revision)
        if cached is not None:
            return cached.decode("utf-8")
        content, content_type = self._read_canonical()
        self._db.write_archive_at_revision(
            self._automaton.project_id, cache_path, self._automaton.revision, content.encode("utf-8"), content_type,
        )
        return content

    @staticmethod
    def _delimiter(header: str) -> str:
        """The column separator this file actually uses, sniffed off its
        header row alone — comma unless another candidate clearly wins."""
        try:
            return csv.Sniffer().sniff(header, delimiters=_DELIMITERS).delimiter
        except csv.Error:
            return ","

    def _project(self, lines: list[str], keys: list[str]) -> str:
        """`lines` (header first) reduced to just the `keys` columns, in
        that order, re-emitted with the file's own delimiter. An unknown
        column name is reported as text — the model asked for it and can
        correct itself — never raised."""
        delimiter = self._delimiter(lines[0])
        rows = list(csv.reader(lines, delimiter=delimiter))
        header = [column.strip() for column in rows[0]]
        unknown = [key for key in keys if key not in header]
        if unknown:
            return (
                f"error: unknown column(s) {', '.join(repr(key) for key in unknown)} — "
                f"available: {', '.join(header)}"
            )
        indexes = [header.index(key) for key in keys]
        out = io.StringIO()
        writer = csv.writer(out, delimiter=delimiter, lineterminator="\n")
        for row in rows:
            writer.writerow([row[index] if index < len(row) else "" for index in indexes])
        return out.getvalue()

    def _matches(self, values: tuple[str, ...]) -> tuple[list[str], list[str]] | None:
        """(header line, matching data lines) for `values`' own filter, or
        None for an empty file — the one piece every read here shares."""
        lines = self._read_text().splitlines(keepends=True)
        if not lines:
            return None
        needles = [value.lower() for value in values]
        matches = [line for line in lines[1:] if all(needle in line.lower() for needle in needles)]
        return [lines[0]], matches

    def select_rows_containing(self, *values: str) -> str:
        found = self._matches(values)
        if found is None:
            return ""
        header, matches = found
        if not matches:
            return ""
        return self._bounded(header[0] + "".join(matches), header=header[0])

    def select_rows_where(self, column: str, operator: str, value: str, *strings: str) -> str:
        if operator not in OPERATORS:
            return f"error: unknown operator {operator!r} — available: {', '.join(OPERATORS)}"
        return self._rows_where_column(column, ColumnComparison(operator, value), strings)

    def select_rows_in_range(self, column: str, start: str, end: str, *strings: str) -> str:
        return self._rows_where_column(column, ColumnRange(start, end), strings)

    def _rows_where_column(
        self, column: str, condition: ColumnComparison | ColumnRange, strings: tuple[str, ...] = (),
    ) -> str:
        found = self._matches(strings)
        if found is None:
            return ""
        header, rows = found
        delimiter = self._delimiter(header[0])
        names = [name.strip() for name in next(csv.reader(header, delimiter=delimiter))]
        if column not in names:
            return f"error: unknown column(s) {column!r} — available: {', '.join(names)}"
        index = names.index(column)
        matches = []
        for line in rows:
            cells = next(csv.reader([line], delimiter=delimiter), [])
            if index < len(cells) and condition.matches(cells[index]):
                matches.append(line)
        if not matches:
            return ""
        return self._bounded(header[0] + "".join(matches), header=header[0])

    def value(self, *values: str, key: str) -> str:
        found = self._matches(values)
        if found is None:
            return ""
        header, matches = found
        if not matches:
            return ""
        projected = self._project([header[0], matches[0]], [key])
        if projected.startswith("error:"):
            return projected
        return projected.splitlines()[1]
