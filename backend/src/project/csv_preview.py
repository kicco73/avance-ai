"""Renders a source's own CSV content as a Markdown table — the design
view's Source content panel (see ProjectEditor.get_source_content_preview)
sends this through the same renderMarkdown() a .md attachment's own
Preview segment already uses, rather than reimplementing table rendering
client-side. Pure text transform, no I/O."""
from __future__ import annotations

import csv
import io


def render_csv_as_markdown_table(content: str) -> str:
    rows = list(csv.reader(io.StringIO(content)))
    if not rows:
        return "*(empty)*"
    width = max(len(row) for row in rows)

    def escape(value: str) -> str:
        return value.replace("|", "\\|").replace("\n", " ")

    def render_row(row: list[str]) -> str:
        padded = row + [""] * (width - len(row))
        return "| " + " | ".join(escape(value) for value in padded) + " |"

    header, *data_rows = rows
    lines = [render_row(header), "| " + " | ".join(["---"] * width) + " |"]
    lines.extend(render_row(row) for row in data_rows)
    return "\n".join(lines)
