from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Iterator, Mapping
from typing import Any

from ruamel.yaml import YAMLError
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import FoldedScalarString, LiteralScalarString

from automaton.automaton_yaml_editor import round_trip_yaml

_BOOLEAN_OPERATORS = frozenset({"and", "or"})
_LAYOUT_TOKENS = frozenset({tokenize.NEWLINE, tokenize.NL, tokenize.ENDMARKER})
_LINE_BREAK = re.compile(r"\s*\n\s*")


class IndexYmlFormatter:

    def format(self, text: str) -> str:
        raw = _load(text)
        if not isinstance(raw, CommentedMap):
            return text
        for action in _triggered_actions(raw):
            action["trigger"] = _TriggerLayout(action["trigger"]).value()
        _MultilineStrings().apply(raw)
        stream = io.StringIO()
        round_trip_yaml().dump(raw, stream)
        formatted = _Spacing(stream.getvalue()).text()
        return {True: formatted, False: text}[_meaning(text) == _meaning(formatted)]


def _load(text: str) -> Any:
    try:
        return round_trip_yaml().load(text)
    except YAMLError:
        return None


def _triggered_actions(raw: Mapping) -> Iterator[Any]:
    states = raw.get("states")
    for state in states.values() if isinstance(states, Mapping) else []:
        actions = state.get("actions") if isinstance(state, Mapping) else None
        for action in actions if isinstance(actions, list) else []:
            if isinstance(action, Mapping) and isinstance(action.get("trigger"), str):
                yield action


class _TriggerLayout:

    def __init__(self, trigger: str) -> None:
        self._trigger = trigger
        self._expression = _LINE_BREAK.sub(" ", trigger.strip())

    def value(self) -> str:
        tokens = self._tokens()
        breaks = [token.end[1] for token in tokens if token.type == tokenize.NAME and token.string in _BOOLEAN_OPERATORS]
        if not breaks:
            return self._trigger
        bounds = zip([0, *breaks], [*breaks, len(self._expression)])
        lines = [self._expression[start:end].strip() for start, end in bounds]
        if not _wrapped(tokens):
            lines[0], lines[-1] = f"({lines[0]}", f"{lines[-1]})"
        folded = FoldedScalarString(" ".join(lines))
        folded.fold_pos = [sum(len(line) + 1 for line in lines[:index]) - 1 for index in range(1, len(lines))]
        return folded

    def _tokens(self) -> list[tokenize.TokenInfo]:
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(self._expression).readline))
        except (tokenize.TokenError, SyntaxError):
            return []
        return [token for token in tokens if token.type not in _LAYOUT_TOKENS]


def _wrapped(tokens: list[tokenize.TokenInfo]) -> bool:
    depth = 0
    for index, token in enumerate(tokens):
        depth += {"(": 1, ")": -1}.get(token.string, 0) if token.type == tokenize.OP else 0
        if depth == 0:
            return index == len(tokens) - 1 and tokens[0].string == "("
    return False


class _MultilineStrings:

    def apply(self, node: Any) -> None:
        for key, value in _children(node):
            if isinstance(value, str) and "\n" in value and not isinstance(value, (LiteralScalarString, FoldedScalarString)):
                node[key] = LiteralScalarString(value)
            self.apply(value)


def _children(node: object) -> list[tuple]:
    if isinstance(node, Mapping):
        return list(node.items())
    if isinstance(node, list):
        return list(enumerate(node))
    return []


class _Spacing:

    def __init__(self, text: str) -> None:
        self._lines = text.split("\n")

    def text(self) -> str:
        spaced: list[str] = []
        openings = self._openings()
        for index, line in enumerate(self._lines):
            if index in openings:
                while spaced and not spaced[-1].strip():
                    spaced.pop()
                spaced.append("")
            spaced.append(line)
        return "\n".join(spaced).rstrip("\n") + "\n"

    def _openings(self) -> set[int]:
        sections: list[int] = []
        states: list[int] = []
        section = None
        for index, line in enumerate(self._lines):
            if _is_key(line, 0):
                sections.append(index)
                section = line.split(":", 1)[0].strip("\"' ")
            elif section == "states" and _is_key(line, 2):
                states.append(index)
        return {self._leading_comments(index) for index in [*sections[1:], *states[1:]]}

    def _leading_comments(self, index: int) -> int:
        indent = _indent(self._lines[index])
        while index > 0 and self._lines[index - 1].lstrip().startswith("#") and _indent(self._lines[index - 1]) == indent:
            index -= 1
        return index


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_key(line: str, indent: int) -> bool:
    return _indent(line) == indent and line.strip() != "" and line.lstrip()[0] not in "#-."


def _meaning(text: str) -> object:
    raw = _plain(_load(text))
    for action in _triggered_actions(raw) if isinstance(raw, Mapping) else []:
        action["trigger"] = _expression_meaning(action["trigger"])
    return raw


def _plain(node: object) -> object:
    if isinstance(node, Mapping):
        return {str(key): _plain(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_plain(value) for value in node]
    if isinstance(node, str):
        return str(node)
    return node


def _expression_meaning(trigger: str) -> str:
    expression = _LINE_BREAK.sub(" ", trigger.strip())
    try:
        return ast.dump(ast.parse(expression, mode="eval"))
    except SyntaxError:
        return expression
