from __future__ import annotations

import re

from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)

class MarkdownRepairer:
    """
    Conservative repairer for LLM-generated Markdown.

    Input/output are Markdown strings only.
    No HTML parsing or HTML transformation is performed.
    """

    _DASH_TRANSLATION = str.maketrans({
        "—": "-",
        "–": "-",
        "−": "-",
    })

    _FENCE_RE = re.compile(
        r"^(?P<indent> {0,3})(?P<char>`|~)(?P<marks>(?P=char){2,})(?P<info>.*)$"
    )

    _TABLE_SEPARATOR_RE = re.compile(
        r"^:?-{3,}:?$"
    )

    def repair(self, markdown: str) -> str:
        if not markdown:
            return markdown

        lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")

        result: list[str] = []
        normal_lines: list[str] = []

        in_fence = False
        fence_char = ""
        fence_length = 0

        def flush_normal() -> None:
            if not normal_lines:
                return

            result.extend(self._repair_normal_lines(normal_lines))
            normal_lines.clear()

        for line in lines:
            fence = self._parse_fence(line)

            if not in_fence:
                if fence is not None:
                    flush_normal()

                    result.append(line)
                    in_fence = True
                    fence_char, fence_length = fence
                else:
                    normal_lines.append(line)

                continue

            result.append(line)

            if self._is_closing_fence(
                line,
                fence_char,
                fence_length,
            ):
                in_fence = False
                fence_char = ""
                fence_length = 0

        flush_normal()

        if in_fence:
            result.append(fence_char * fence_length)

        return self._cleanup(result)


    def _repair_normal_lines(self, lines: list[str]) -> list[str]:
        lines = self._detach_attached_table_headers(lines)
        lines = self._repair_tables(lines)
        lines = self._repair_lists(lines)
        lines = self._repair_emphasis(lines)
        return lines


    def _detach_attached_table_headers(
        self,
        lines: list[str],
    ) -> list[str]:
        """
        Turns:

            prose...|Título | Tema
            — | —

        into:

            prose...

            Título | Tema
            — | —
        """

        result: list[str] = []
        i = 0

        while i < len(lines):
            line = lines[i]

            if i + 1 < len(lines):
                separator = lines[i + 1]

                split = self._find_attached_table_header(
                    line,
                    separator,
                )

                if split is not None:
                    prose, header = split

                    if prose.strip():
                        result.append(prose.rstrip())
                        result.append("")

                    result.append(header)

                    i += 1
                    continue

            result.append(line)
            i += 1

        return result

    def _find_attached_table_header(
        self,
        line: str,
        separator: str,
    ) -> tuple[str, str] | None:
        separator_cells = self._split_table_row(separator)

        if separator_cells is None:
            return None

        if not self._is_separator_row(separator_cells):
            return None

        if len(separator_cells) < 2:
            return None

        positions = self._pipe_positions(line)

        for position in positions:
            prose = line[:position].rstrip()
            header = line[position:].strip()

            if not prose or not header:
                continue

            header_cells = self._split_table_row(header)

            if header_cells is None:
                continue

            if len(header_cells) != len(separator_cells):
                continue

            if len(header_cells) < 2:
                continue

            return prose, self._format_header_row(header_cells)

        return None

    def _repair_tables(self, lines: list[str]) -> list[str]:
        result: list[str] = []
        i = 0

        while i < len(lines):
            header = self._split_table_row(lines[i])

            if header is None or len(header) < 2:
                result.append(lines[i])
                i += 1
                continue


            if i + 1 < len(lines):
                separator = self._split_table_row(lines[i + 1])

                if (
                    separator is not None
                    and len(separator) == len(header)
                    and self._is_separator_row(separator)
                ):
                    table, next_index = self._consume_table(
                        lines,
                        i,
                        header,
                        separator,
                    )

                    result.extend(table)
                    i = next_index
                    continue


            if self._looks_like_table_without_separator(
                lines,
                i,
                header,
            ):
                separator = ["---"] * len(header)

                table, next_index = self._consume_table_without_separator(
                    lines,
                    i,
                    header,
                    separator,
                )

                result.extend(table)
                i = next_index
                continue

            result.append(lines[i])
            i += 1

        return result

    def _consume_table(
        self,
        lines: list[str],
        start: int,
        header: list[str],
        separator: list[str],
    ) -> tuple[list[str], int]:
        header = self._normalize_bold_header(header)

        result = [
            self._format_header_row(header),
            self._format_separator_row(separator),
        ]

        i = start + 2

        while i < len(lines):
            if not lines[i].strip():
                break

            cells = self._split_table_row(lines[i])

            if cells is None or len(cells) != len(header):
                break

            result.append(self._format_table_row(cells))
            i += 1

        return result, i

    def _consume_table_without_separator(
        self,
        lines: list[str],
        start: int,
        header: list[str],
        separator: list[str],
    ) -> tuple[list[str], int]:
        header = self._normalize_bold_header(header)

        result = [
            self._format_header_row(header),
            self._format_separator_row(separator),
        ]

        i = start + 1

        while i < len(lines):
            if not lines[i].strip():
                break

            cells = self._split_table_row(lines[i])

            if cells is None or len(cells) != len(header):
                break

            result.append(self._format_table_row(cells))
            i += 1

        return result, i

    def _looks_like_table_without_separator(
        self,
        lines: list[str],
        index: int,
        header: list[str],
    ) -> bool:
        if index + 1 >= len(lines):
            return False

        if len(header) < 2:
            return False

        next_cells = self._split_table_row(lines[index + 1])

        if next_cells is None:
            return False

        if len(next_cells) != len(header):
            return False

        current = lines[index].strip()

        if current.startswith("|") or current.endswith("|"):
            return True

        if index + 2 >= len(lines):
            return False

        third_cells = self._split_table_row(lines[index + 2])

        return (
            third_cells is not None
            and len(third_cells) == len(header)
        )

    def _split_table_row(self, line: str) -> list[str] | None:
        """
        Split a pipe-separated Markdown row.

        Supports:
            A | B
            | A | B |
            **A | B**
            `a|b` | C
            A \\| B | C

        Pipes inside inline code and escaped pipes are not separators.
        """

        stripped = line.strip()

        if not stripped:
            return None

        if not self._has_unescaped_pipe(stripped):
            return None

        cells: list[str] = []
        current: list[str] = []

        escaped = False
        in_code = False

        i = 0

        while i < len(stripped):
            char = stripped[i]

            if escaped:
                current.append(char)
                escaped = False
                i += 1
                continue

            if char == "\\":
                current.append(char)
                escaped = True
                i += 1
                continue

            if char == "`":
                in_code = not in_code
                current.append(char)
                i += 1
                continue

            if char == "|" and not in_code:
                cells.append("".join(current).strip())
                current = []
                i += 1
                continue

            current.append(char)
            i += 1

        cells.append("".join(current).strip())

        if stripped.startswith("|") and cells and cells[0] == "":
            cells.pop(0)

        if stripped.endswith("|") and cells and cells[-1] == "":
            cells.pop()

        if len(cells) < 2:
            return None

        if any(cell == "" for cell in cells):
            return None

        return cells

    def _normalize_bold_header(
        self,
        cells: list[str],
    ) -> list[str]:
        """
        Converts:

            **Título | Tema**

        from:

            ["**Título", "Tema**"]

        into:

            ["**Título**", "**Tema**"]
        """

        if len(cells) < 2:
            return cells

        first = cells[0].strip()
        last = cells[-1].strip()

        if not (
            first.startswith("**")
            and last.endswith("**")
        ):
            return cells

        normalized = list(cells)

        normalized[0] = first[2:].strip()
        normalized[-1] = last[:-2].strip()

        return [
            cell if (
                cell.startswith("**")
                and cell.endswith("**")
            )
            else f"**{cell}**"
            for cell in normalized
        ]

    def _is_separator_row(self, cells: list[str]) -> bool:
        for cell in cells:
            normalized = cell.translate(self._DASH_TRANSLATION).strip()

            if not self._TABLE_SEPARATOR_RE.fullmatch(normalized):
                return False

        return True

    def _format_header_row(self, cells: list[str]) -> str:
        return self._format_table_row(cells)

    def _format_table_row(self, cells: list[str]) -> str:
        return "| " + " | ".join(
            cell.strip()
            for cell in cells
        ) + " |"

    def _format_separator_row(self, cells: list[str]) -> str:
        normalized: list[str] = []

        for cell in cells:
            cell = cell.translate(self._DASH_TRANSLATION).strip()

            left = cell.startswith(":")
            right = cell.endswith(":")

            core = cell.strip(":")

            core = "-" * max(3, len(core))

            if left:
                core = ":" + core

            if right:
                core += ":"

            normalized.append(core)

        return self._format_table_row(normalized)

    def _pipe_positions(self, line: str) -> list[int]:
        positions: list[int] = []

        escaped = False
        in_code = False

        for i, char in enumerate(line):
            if escaped:
                escaped = False
                continue

            if char == "\\":
                escaped = True
                continue

            if char == "`":
                in_code = not in_code
                continue

            if char == "|" and not in_code:
                positions.append(i)

        return positions

    def _has_unescaped_pipe(self, line: str) -> bool:
        return bool(self._pipe_positions(line))


    def _parse_fence(self, line: str) -> tuple[str, int] | None:
        match = self._FENCE_RE.match(line)

        if match is None:
            return None

        char = match.group("char")
        marks = match.group("marks")

        return char, len(marks)

    def _is_closing_fence(
        self,
        line: str,
        fence_char: str,
        fence_length: int,
    ) -> bool:
        pattern = (
            rf"^ {{0,3}}"
            rf"{re.escape(fence_char)}"
            rf"{{{fence_length},}}"
            rf"\s*$"
        )

        return re.match(pattern, line) is not None


    def _repair_lists(self, lines: list[str]) -> list[str]:
        result: list[str] = []

        for line in lines:
            line = re.sub(
                r"^\t+",
                lambda match: "  " * len(match.group()),
                line,
            )

            match = re.match(
                r"^( {5,})([-+*])(\s+)(.*)$",
                line,
            )

            if match:
                indentation = match.group(1)
                marker = match.group(2)
                spacing = match.group(3)
                content = match.group(4)

                level = min(len(indentation) // 2, 8)

                line = (
                    ("  " * level)
                    + marker
                    + spacing
                    + content
                )

            result.append(line)

        return result


    def _repair_emphasis(self, lines: list[str]) -> list[str]:
        return [
            self._repair_emphasis_line(line)
            for line in lines
        ]

    def _repair_emphasis_line(self, line: str) -> str:
        if not line.strip():
            return line

        if re.match(r"^\s*[-+*]\s+", line):
            return line

        if "://" in line:
            return line

        protected: list[str] = []

        def protect(match: re.Match[str]) -> str:
            protected.append(match.group(0))
            return f"\x00{len(protected) - 1}\x00"

        masked = re.sub(
            r"`[^`]*`",
            protect,
            line,
        )

        for marker in ("**", "__"):
            if masked.count(marker) % 2 == 1:
                masked += marker

        if (
            masked.count("*") == 1
            and not masked.lstrip().startswith("*")
            and not masked.rstrip().endswith("*")
        ):
            masked += "*"

        if (
            masked.count("_") == 1
            and not re.search(r"\w_\w", masked)
            and not masked.rstrip().endswith("_")
        ):
            masked += "_"

        def restore(match: re.Match[str]) -> str:
            return protected[int(match.group(1))]

        return re.sub(
            r"\x00(\d+)\x00",
            restore,
            masked,
        )


    def _cleanup(self, lines: list[str]) -> str:
        lines = [
            line.rstrip()
            for line in lines
        ]

        result: list[str] = []
        previous_blank = False

        for line in lines:
            if not line.strip():
                if not previous_blank:
                    result.append("")

                previous_blank = True
                continue

            result.append(line)
            previous_blank = False

        return "\n".join(result).strip()