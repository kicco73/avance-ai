from __future__ import annotations

import ast
import textwrap


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
    def flattened_statements(cls, statements: list[tuple[int, str]]) -> list[tuple[int, str]]:
        flat: list[tuple[int, str]] = []
        for line_number, statement in statements:
            branches = cls.if_branches(statement, line_number)
            if branches is None:
                flat.append((line_number, statement))
                continue
            for condition, body in branches:
                if condition is not None:
                    flat.append((line_number, condition))
                flat.extend(cls.flattened_statements(body))
        return flat
