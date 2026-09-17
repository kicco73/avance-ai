from __future__ import annotations

import re


class MarkdownRepairer:
    """Best-effort sanitizer for LLM-generated Markdown."""

    _FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
    _LIST_RE = re.compile(r"^(\s*)([-+*]|\d+[.)])\s+(.*)$")
    _TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")

    def repair(self, markdown: str) -> str:
        if not markdown:
            return markdown

        markdown = markdown.replace("\r\n", "\n").replace("\r", "\n")

        lines = markdown.split("\n")

        lines = self._repair_attached_tables(lines)

        lines = self._repair_fences(lines)
        lines = self._repair_tables(lines)
        lines = self._repair_lists(lines)
        lines = self._repair_emphasis(lines)

        return "\n".join(lines).strip()


    def _repair_attached_tables(self, lines: list[str]) -> list[str]:
        """
        Turns:

            Some text: | A | B |
            | — | — |
            | x | y |

        into:

            Some text:

            | A | B |
            | --- | --- |
            | x | y |
        """

        result: list[str] = []
        i = 0

        while i < len(lines):
            line = lines[i]

            if "|" not in line or i + 1 >= len(lines):
                result.append(line)
                i += 1
                continue

            found = False

            for match in re.finditer(r"\|", line):
                position = match.start()

                prose = line[:position].rstrip()
                table_header = line[position:].strip()

                if not prose:
                    continue

                if not self._is_table_row(table_header):
                    continue

                if not self._is_table_separator(lines[i + 1]):
                    continue

                result.append(prose)
                result.append("")
                result.append(table_header)

                i += 1
                found = True
                break

            if not found:
                result.append(line)
                i += 1

        return result


    def _repair_fences(self, lines: list[str]) -> list[str]:
        result: list[str] = []

        fence_char: str | None = None
        fence_length = 0

        for line in lines:
            match = self._FENCE_RE.match(line)

            if match:
                marker = match.group(1)
                char = marker[0]
                length = len(marker)

                if fence_char is None:
                    fence_char = char
                    fence_length = length
                elif char == fence_char and length >= fence_length:
                    fence_char = None
                    fence_length = 0

                result.append(line)
                continue

            result.append(line)

        if fence_char is not None:
            result.append(fence_char * fence_length)

        return result


    def _repair_tables(self, lines: list[str]) -> list[str]:
        result: list[str] = []
        i = 0

        while i < len(lines):
            if i + 1 >= len(lines):
                result.append(lines[i])
                break

            header = lines[i]
            separator = lines[i + 1]

            if (
                self._is_table_row(header)
                and self._is_table_separator(separator)
            ):
                columns = len(self._split_row(header))

                result.append(
                    self._normalize_row(header, columns)
                )
                result.append(
                    self._normalize_separator(separator, columns)
                )

                i += 2

                while i < len(lines):
                    row = lines[i]

                    if not self._is_table_row(row):
                        break

                    cells = self._split_row(row)

                    if len(cells) < 2:
                        break

                    result.append(
                        self._normalize_row(row, columns)
                    )
                    i += 1

                continue

            if (
                self._is_table_row(header)
                and self._is_table_row(separator)
            ):
                header_cells = self._split_row(header)
                second_cells = self._split_row(separator)

                if (
                    len(header_cells) >= 2
                    and len(header_cells) == len(second_cells)
                ):
                    columns = len(header_cells)

                    result.append(
                        self._normalize_row(header, columns)
                    )
                    result.append(
                        self._make_separator(columns)
                    )
                    result.append(
                        self._normalize_row(separator, columns)
                    )

                    i += 2

                    while i < len(lines):
                        row = lines[i]

                        if not self._is_table_row(row):
                            break

                        cells = self._split_row(row)

                        if len(cells) != columns:
                            break

                        result.append(
                            self._normalize_row(row, columns)
                        )
                        i += 1

                    continue

            result.append(lines[i])
            i += 1

        return result

    def _is_table_row(self, line: str) -> bool:
        if self._FENCE_RE.match(line):
            return False

        stripped = line.strip()

        if not stripped or "|" not in stripped:
            return False

        return len(self._split_row(stripped)) >= 2

    def _is_table_separator(self, line: str) -> bool:
        cells = self._split_row(line)

        if len(cells) < 2:
            return False

        for cell in cells:
            cell = (
                cell.strip()
                .replace("—", "-")
                .replace("–", "-")
                .replace("−", "-")
            )

            if not self._TABLE_SEPARATOR_CELL_RE.fullmatch(cell):
                return False

        return True

    @staticmethod
    def _split_row(line: str) -> list[str]:
        line = line.strip()

        if line.startswith("|"):
            line = line[1:]

        if line.endswith("|") and not line.endswith(r"\|"):
            line = line[:-1]

        return [cell.strip() for cell in line.split("|")]

    def _normalize_row(self, line: str, columns: int) -> str:
        cells = self._split_row(line)

        if len(cells) < columns:
            cells.extend([""] * (columns - len(cells)))

        elif len(cells) > columns:
            cells = cells[:columns - 1] + [
                " | ".join(cells[columns - 1:])
            ]

        return "| " + " | ".join(cells) + " |"

    def _normalize_separator(self, line: str, columns: int) -> str:
        cells = self._split_row(line)
        normalized: list[str] = []

        for cell in cells[:columns]:
            cell = (
                cell.strip()
                .replace("—", "-")
                .replace("–", "-")
                .replace("−", "-")
            )

            left = cell.startswith(":")
            right = cell.endswith(":")

            if left and right:
                normalized.append(":---:")
            elif left:
                normalized.append(":---")
            elif right:
                normalized.append("---:")
            else:
                normalized.append("---")

        normalized.extend(["---"] * (columns - len(normalized)))

        return "| " + " | ".join(normalized) + " |"

    @staticmethod
    def _make_separator(columns: int) -> str:
        return "| " + " | ".join(["---"] * columns) + " |"


    def _repair_lists(self, lines: list[str]) -> list[str]:
        result: list[str] = []

        in_fence = False
        fence_char: str | None = None
        previous_list_indent: int | None = None

        for line in lines:
            fence = self._FENCE_RE.match(line)

            if fence:
                marker = fence.group(1)

                if fence_char is None:
                    fence_char = marker[0]
                    in_fence = True
                elif marker[0] == fence_char:
                    fence_char = None
                    in_fence = False

                result.append(line)
                continue

            if in_fence:
                result.append(line)
                continue

            match = self._LIST_RE.match(line)

            if not match:
                result.append(line)
                previous_list_indent = None
                continue

            indent, marker, content = match.groups()
            indent_length = len(indent.expandtabs(2))

            indent_length = (indent_length // 2) * 2

            if previous_list_indent is None:
                indent_length = 0
            elif indent_length > previous_list_indent + 2:
                indent_length = previous_list_indent + 2

            result.append(
                f"{' ' * indent_length}{marker} {content}"
            )

            previous_list_indent = indent_length

        return result


    def _repair_emphasis(self, lines: list[str]) -> list[str]:
        result: list[str] = []

        in_fence = False
        fence_char: str | None = None

        for line in lines:
            fence = self._FENCE_RE.match(line)

            if fence:
                marker = fence.group(1)

                if fence_char is None:
                    fence_char = marker[0]
                    in_fence = True
                elif marker[0] == fence_char:
                    fence_char = None
                    in_fence = False

                result.append(line)
                continue

            if in_fence:
                result.append(line)
                continue

            line = self._balance_marker(line, "**")
            line = self._balance_marker(line, "__")

            if line.count("*") == 1:
                line += "*"

            if line.count("_") == 1:
                position = line.find("_")

                if not (
                    position > 0
                    and position + 1 < len(line)
                    and line[position - 1].isalnum()
                    and line[position + 1].isalnum()
                ):
                    line += "_"

            result.append(line)

        return result

    @staticmethod
    def _balance_marker(line: str, marker: str) -> str:
        if line.count(marker) % 2:
            return line + marker

        return line