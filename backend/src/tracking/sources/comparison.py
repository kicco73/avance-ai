from __future__ import annotations

from datetime import datetime

OPERATORS = ("=", "!=", ">", ">=", "<", "<=")


def _as_number(text: str) -> float | None:
    try:
        return float(text.strip())
    except ValueError:
        return None


def _as_moment(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.strip().replace(" ", "T"))
    except ValueError:
        return None


def _comparable(cell: str, value: str) -> tuple:
    numbers = (_as_number(cell), _as_number(value))
    if None not in numbers:
        return numbers
    moments = (_as_moment(cell), _as_moment(value))
    if None not in moments:
        return moments
    return (cell.strip().lower(), value.strip().lower())


def compare(cell: str, operator: str, value: str) -> bool:
    left, right = _comparable(cell, value)
    if operator == "=":
        return left == right
    if operator == "!=":
        return left != right
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    raise ValueError(f"unknown operator {operator!r} — use one of {', '.join(OPERATORS)}.")


class ColumnComparison:
    def __init__(self, operator: str, value: str) -> None:
        self._operator = operator
        self._value = value

    def matches(self, cell: str) -> bool:
        return compare(cell, self._operator, self._value)


class ColumnRange:
    def __init__(self, start: str, end: str) -> None:
        self._start = start
        self._end = end

    def matches(self, cell: str) -> bool:
        return compare(cell, ">=", self._start) and compare(cell, "<=", self._end)
