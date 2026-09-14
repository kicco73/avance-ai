#!/usr/bin/env python3
import argparse
import re
import sys
import tokenize
from pathlib import Path


KEPT_MARKERS = ("XXX", "FIXME")

KEPT_DIRECTIVES = (
    "noqa",
    "type:",
    "pragma:",
    "pyright:",
    "mypy:",
    "flake8:",
    "ruff:",
    "nosec",
    "pylint:",
    "eslint",
    "prettier-ignore",
    "@ts-",
    "istanbul ignore",
    "v8 ignore",
    "c8 ignore",
    "biome-ignore",
    "stylelint-",
    "webpackChunkName",
    "vite-ignore",
    "global ",
    "jshint",
    "sourceMappingURL",
    "#__PURE__",
    "@vite-ignore",
)

DECORATION = "#*-=~_/!<>| \t"

CODING_COOKIE = re.compile(r"coding[:=]\s*[-\w.]+")

REGEX_KEYWORDS = frozenset(
    (
        "return",
        "typeof",
        "instanceof",
        "in",
        "of",
        "case",
        "do",
        "else",
        "yield",
        "await",
        "new",
        "delete",
        "void",
        "throw",
        "default",
    )
)

REGEX_OPENERS = "(,=:[!&|?{};+-*%^~<>"

SENTINEL = "\x00"

CODE = "code"

STYLES = "styles"

MARKUP = "markup"


class CommentPolicy:
    def keeps(self, body):
        raw = body.strip()
        text = raw.lstrip(DECORATION).strip()
        return (
            text.startswith(KEPT_MARKERS)
            or raw.startswith(KEPT_DIRECTIVES)
            or text.startswith(KEPT_DIRECTIVES)
        )


class Redactor:
    def __init__(self, text):
        self._text = text

    def without(self, spans):
        marked = list(self._text)
        for start, end in spans:
            for index in range(start, end):
                marked[index] = SENTINEL
        kept = []
        for line in "".join(marked).splitlines(keepends=True):
            if SENTINEL not in line:
                kept.append(line)
                continue
            ending = line[len(line.rstrip("\r\n")):]
            body = line[: len(line) - len(ending)].replace(SENTINEL, "")
            if body.strip():
                kept.append(body.rstrip() + ending)
        return "".join(kept)


class PythonSource:
    SUFFIXES = (".py", ".pyi")
    FAMILY = CODE

    def __init__(self, text, policy, selection=None):
        self._text = text
        self._policy = policy

    def comment_spans(self):
        starts = self._line_starts()
        spans = []
        for token in tokenize.generate_tokens(iter(self._text.splitlines(keepends=True)).__next__):
            if token.type != tokenize.COMMENT:
                continue
            row, column = token.start
            body = token.string[1:]
            if self._is_header(row, token.string):
                continue
            if self._policy.keeps(body):
                continue
            start = starts[row - 1] + column
            spans.append((start, start + len(token.string)))
        return spans

    def _is_header(self, row, comment):
        if row == 1 and comment.startswith("#!"):
            return True
        return row <= 2 and CODING_COOKIE.search(comment) is not None

    def _line_starts(self):
        starts = [0]
        for line in self._text.splitlines(keepends=True):
            starts.append(starts[-1] + len(line))
        return starts


class CodeScanner:
    line_comments = True
    regex_literals = True
    quotes = "'\"`"

    def __init__(self, text, offset=0):
        self._text = text
        self._offset = offset

    def comments(self):
        text = self._text
        size = len(text)
        index = 0
        while index < size:
            char = text[index]
            if char in self.quotes:
                index = self._skip_string(index)
                continue
            if char != "/" or index + 1 >= size:
                index += 1
                continue
            following = text[index + 1]
            if following == "/" and self.line_comments:
                end = text.find("\n", index)
                end = size if end < 0 else end
                yield self._offset + index, self._offset + end, text[index + 2 : end]
                index = end
                continue
            if following == "*":
                closing = text.find("*/", index + 2)
                end = size if closing < 0 else closing + 2
                yield self._offset + index, self._offset + end, text[index + 2 : max(index + 2, end - 2)]
                index = end
                continue
            if self.regex_literals and self._starts_regex(index):
                index = self._skip_regex(index)
                continue
            index += 1

    def _skip_string(self, index):
        text = self._text
        size = len(text)
        quote = text[index]
        cursor = index + 1
        while cursor < size:
            char = text[cursor]
            if char == "\\":
                cursor += 2
                continue
            if char == quote:
                return cursor + 1
            if char == "\n" and quote != "`":
                return cursor
            cursor += 1
        return size

    def _skip_regex(self, index):
        text = self._text
        size = len(text)
        cursor = index + 1
        in_class = False
        while cursor < size:
            char = text[cursor]
            if char == "\\":
                cursor += 2
                continue
            if char == "\n":
                return index + 1
            if char == "[":
                in_class = True
            elif char == "]":
                in_class = False
            elif char == "/" and not in_class:
                return cursor + 1
            cursor += 1
        return size

    def _starts_regex(self, index):
        text = self._text
        cursor = index - 1
        while cursor >= 0 and text[cursor] in " \t\r\n":
            cursor -= 1
        if cursor < 0:
            return True
        char = text[cursor]
        if char in REGEX_OPENERS:
            return True
        if not (char.isalnum() or char == "_" or char == "$"):
            return False
        end = cursor + 1
        while cursor >= 0 and (text[cursor].isalnum() or text[cursor] in "_$"):
            cursor -= 1
        return text[cursor + 1 : end] in REGEX_KEYWORDS


class StyleScanner(CodeScanner):
    line_comments = False
    regex_literals = False
    quotes = "'\""


class MarkupScanner:
    def __init__(self, text, offset=0):
        self._text = text
        self._offset = offset

    def comments(self):
        text = self._text
        index = text.find("<!--")
        while index >= 0:
            closing = text.find("-->", index + 4)
            end = len(text) if closing < 0 else closing + 3
            yield self._offset + index, self._offset + end, text[index + 4 : max(index + 4, end - 3)]
            index = text.find("<!--", end)


class ScannedSource:
    def __init__(self, text, policy, scanner):
        self._text = text
        self._policy = policy
        self._scanner = scanner

    def comment_spans(self):
        return [
            (start, end)
            for start, end, body in self._scanner(self._text).comments()
            if not self._policy.keeps(body)
        ]


class ScriptSource(ScannedSource):
    SUFFIXES = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")
    FAMILY = CODE

    def __init__(self, text, policy, selection):
        super().__init__(text, policy, CodeScanner)


class StyleSource(ScannedSource):
    SUFFIXES = (".css", ".scss", ".less")
    FAMILY = STYLES

    def __init__(self, text, policy, selection):
        super().__init__(text, policy, StyleScanner)


class MarkupSource(ScannedSource):
    SUFFIXES = (".html", ".htm", ".xml", ".svg")
    FAMILY = MARKUP

    def __init__(self, text, policy, selection):
        super().__init__(text, policy, MarkupScanner)


class VueSource:
    SUFFIXES = (".vue",)
    FAMILY = CODE

    BLOCK = re.compile(r"^<(template|script|style)(?:\s[^>]*)?>\n(.*?)^</\1>", re.M | re.S)

    BLOCKS = {
        "template": (MarkupScanner, MARKUP),
        "script": (CodeScanner, CODE),
        "style": (StyleScanner, STYLES),
    }

    def __init__(self, text, policy, selection):
        self._text = text
        self._policy = policy
        self._selection = selection

    def comment_spans(self):
        spans = []
        for block in self.BLOCK.finditer(self._text):
            scanner, family = self.BLOCKS[block.group(1)]
            if not self._selection.allows(family):
                continue
            found = scanner(block.group(2), block.start(2)).comments()
            spans.extend((start, end) for start, end, body in found if not self._policy.keeps(body))
        return spans


class Selection:
    def __init__(self, families=()):
        self._families = frozenset(families) | {CODE}

    def allows(self, family):
        return family in self._families

    def suffixes(self):
        return sorted(
            suffix
            for source in SOURCES
            for suffix in source.SUFFIXES
            if self.allows(source.FAMILY)
        )


SOURCES = (PythonSource, ScriptSource, StyleSource, MarkupSource, VueSource)

BY_SUFFIX = {suffix: source for source in SOURCES for suffix in source.SUFFIXES}


class Stripper:
    def __init__(self, selection=None, policy=None):
        self._selection = selection or Selection()
        self._policy = policy or CommentPolicy()

    def handles(self, path):
        source = BY_SUFFIX.get(Path(path).suffix.lower())
        return source is not None and self._selection.allows(source.FAMILY)

    def stripped(self, path, text):
        source = BY_SUFFIX[Path(path).suffix.lower()](text, self._policy, self._selection)
        return Redactor(text).without(source.comment_spans())


class Run:
    def __init__(self, paths, selection, check, quiet):
        self._paths = paths
        self._check = check
        self._quiet = quiet
        self._stripper = Stripper(selection)
        self._changed = []
        self._failed = []

    def execute(self):
        for name in self._paths:
            self._visit(Path(name))
        self._report()
        return 1 if (self._failed or (self._check and self._changed)) else 0

    def _visit(self, path):
        if not self._stripper.handles(path):
            return
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            self._failed.append((path, error))
            return
        if SENTINEL in text:
            return
        try:
            stripped = self._stripper.stripped(path, text)
        except (tokenize.TokenError, IndentationError, SyntaxError) as error:
            self._failed.append((path, error))
            return
        if stripped == text:
            return
        self._changed.append(path)
        if not self._check:
            path.write_text(stripped, encoding="utf-8")

    def _report(self):
        for path, error in self._failed:
            print(f"strip_comments: {path}: {error}", file=sys.stderr)
        if self._quiet:
            return
        for path in self._changed:
            print(f"{'would strip' if self._check else 'stripped'} {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Remove comments from the given files: code only, unless --styles or --markup "
            "widen it. A comment is kept only when its text "
            f"starts with {' or '.join(KEPT_MARKERS)}, or when it is a tool directive "
            "(noqa, type:, eslint-disable, ...), a shebang or a coding declaration."
        )
    )
    parser.add_argument("files", nargs="+", help=f"files to strip ({', '.join(Selection().suffixes())})")
    parser.add_argument(
        "--styles",
        action="store_true",
        help=f"also strip stylesheets ({', '.join(StyleSource.SUFFIXES)}) and a .vue <style> block",
    )
    parser.add_argument(
        "--markup",
        action="store_true",
        help=f"also strip markup ({', '.join(MarkupSource.SUFFIXES)}) and a .vue <template> block",
    )
    parser.add_argument("--check", action="store_true", help="report what would change, write nothing, exit 1 if any")
    parser.add_argument("--quiet", action="store_true", help="print errors only")
    arguments = parser.parse_args(argv)
    families = [STYLES] if arguments.styles else []
    families += [MARKUP] if arguments.markup else []
    selection = Selection(families)
    return Run(arguments.files, selection, arguments.check, arguments.quiet).execute()


if __name__ == "__main__":
    sys.exit(main())
