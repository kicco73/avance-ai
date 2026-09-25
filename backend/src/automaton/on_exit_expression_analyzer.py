from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NameBinding:
    name: str

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name,)

    def values(self, item: Any) -> dict[str, Any]:
        return {self.name: item}


@dataclass(frozen=True)
class TupleBinding:
    names: tuple[str, ...]

    def values(self, item: Any) -> dict[str, Any]:
        items = tuple(item)
        if len(items) != len(self.names):
            raise ValueError(f"cannot unpack {len(items)} value(s) into {', '.join(self.names)}")
        return dict(zip(self.names, items))


@dataclass(frozen=True)
class OnExitForLoop:
    binding: NameBinding | TupleBinding
    iterable: str
    body: list[tuple[int, str]]


class OnExitExpressionAnalyzer:
    @staticmethod
    def _segments(source: str, statements: list[ast.stmt], line_number: int) -> list[tuple[int, str]]:
        return [
            (line_number + stmt.lineno - 1, textwrap.dedent(ast.get_source_segment(source, stmt, padded=True)))
            for stmt in statements
        ]

    @classmethod
    def if_branches(cls, statement: str, line_number: int) -> list[tuple[str | None, list[tuple[int, str]]]] | None:
        tree = ast.parse(statement, mode="exec")
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.If):
            return None
        branches: list[tuple[str | None, list[tuple[int, str]]]] = []
        rest: list[ast.stmt] = tree.body
        while len(rest) == 1 and isinstance(rest[0], ast.If):
            branches.append((ast.unparse(rest[0].test), cls._segments(statement, rest[0].body, line_number)))
            rest = rest[0].orelse
        if rest:
            branches.append((None, cls._segments(statement, rest, line_number)))
        return branches

    @classmethod
    def for_loop(cls, statement: str, line_number: int) -> OnExitForLoop | None:
        tree = ast.parse(statement, mode="exec")
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.For):
            return None
        loop = tree.body[0]
        if loop.orelse:
            raise ValueError("a 'for' loop in on-exit can't have an 'else' branch.")
        return OnExitForLoop(cls._binding(loop.target), ast.unparse(loop.iter), cls._segments(statement, loop.body, line_number))

    @staticmethod
    def _binding(target: ast.expr) -> NameBinding | TupleBinding:
        if isinstance(target, ast.Name):
            return NameBinding(target.id)
        if isinstance(target, ast.Tuple) and all(isinstance(name, ast.Name) for name in target.elts):
            return TupleBinding(tuple(name.id for name in target.elts))
        raise ValueError("a 'for' loop in on-exit binds plain names only, e.g. 'for name in ...' or 'for a, b in ...'.")

    @classmethod
    def flattened_statements(cls, statements: list[tuple[int, str]]) -> list[tuple[int, str]]:
        flat: list[tuple[int, str]] = []
        for line_number, statement in statements:
            loop = cls.for_loop(statement, line_number)
            if loop is not None:
                flat.append((line_number, loop.iterable))
                flat.extend(cls.flattened_statements(loop.body))
                continue
            branches = cls.if_branches(statement, line_number)
            if branches is None:
                flat.append((line_number, statement))
                continue
            for condition, body in branches:
                if condition is not None:
                    flat.append((line_number, condition))
                flat.extend(cls.flattened_statements(body))
        return flat
