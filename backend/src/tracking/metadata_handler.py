from __future__ import annotations

import csv
import io
import json
from typing import Any, NoReturn

from logging_factory import LoggerFactory
from try_again_error import TryAgainError

logger = LoggerFactory.get_logger(__name__)

# The batch formats' own end-of-output marker (see parse_batch_signals/
# parse_batch_env) — bracketed and lowercase specifically so it can never
# collide with a real turn header ("<N>:", always just digits) or a real
# "key=value" line (always contains "="): "[eof]" is neither.
BATCH_END_MARKER = "[eof]"


class MetadataTurnMismatch(TryAgainError):
    """Raised the instant a batch signals/env response fails to
    demonstrably cover every turn the batch call was asked for — a
    single bad line/row, a missing or extra turn, or a missing
    BATCH_END_MARKER all raise this immediately, on the spot, rather
    than being logged and skipped so parsing can keep going. No partial
    recovery, ever: a response that's wrong or incomplete anywhere is
    wrong as a whole, and whatever was already parsed before the bad
    part is discarded along with it. This mirrors why the formats below
    use strict parsing (a plain json.loads, or an equally strict
    line-by-line grammar) instead of a lenient/best-effort parser — the
    entire point is to fail fast and definitely the moment something is
    off, not to salvage a plausible-looking partial result a truncated
    or malformed response could otherwise pass off as complete. Always a
    model mistake, always worth another attempt (TryAgainError): none of
    these failure modes are guaranteed to repeat on a fresh sample.
    Batch-only: the single-turn parsers below have no turn concept at
    all, so nothing to mismatch."""
    def __init__(self, message: str) -> None:
        super().__init__(message)


def _fail(message: str, raw: str | None) -> NoReturn:
    logger.error(f"{message} -- raw: {raw!r}")
    raise MetadataTurnMismatch(message)


class MetadataHandler(object):
    @staticmethod
    def parse_raw_signals(raw_signals: str | None) -> dict[str, float]:
        """Single-turn format only (a live turn, or TurnByTurnSignalSource's
        one-call-per-turn replay): a JSON object, e.g. '{"mood": 50.2}'."""
        signals: dict[str, Any] = {}
        if not raw_signals:
            return signals
        try:
            signals = json.loads(raw_signals) or {}
            assert isinstance(signals, dict)
        except Exception as exc:
            logger.error(f"{exc} -- raw signal: {raw_signals}")
        return signals

    @staticmethod
    def parse_raw_env(raw_env: str | None) -> dict[str, str]:
        """Single-turn format only (a live turn, or TurnByTurnSignalSource's
        one-call-per-turn replay): one "key: value" pair per line,
        optionally prefixed with "-"; blank lines and anything without a
        ':' are ignored rather than raising — deliberately forgiving,
        since this is model output."""
        env: dict[str, str] = {}
        for line in (raw_env or "").splitlines():
            line = line.strip()
            if line.startswith("-"):
                line = line[1:].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            if key:
                env[key] = value.strip()
        return env

    @staticmethod
    def _turns_in_order(
        kind: str, by_turn: dict[int, dict], expected_turns: int, raw: str | None, terminated: bool,
    ) -> list[dict]:
        """Batch-only check: exactly turns 1..expected_turns must be
        present, no more, no fewer, AND the batch's own end-of-output
        marker must have been seen — turn coverage alone is never enough,
        since a response cut off right before the marker could otherwise
        look complete by coincidence. Returns the turns as a plain list,
        index i = turn i+1. Only ever reached once every individual
        line/row already parsed cleanly (see the two parsers below,
        which raise immediately otherwise) — so a failure here always
        means the turns themselves don't add up, never a malformed entry."""
        actual = set(by_turn.keys())
        expected = set(range(1, expected_turns + 1))
        if actual == expected and terminated:
            return [by_turn[i] for i in range(1, expected_turns + 1)]
        if actual == expected:
            message = f"{kind}: got all {expected_turns} turns but no {BATCH_END_MARKER} marker — cannot trust it's complete"
        else:
            message = f"{kind}: expected turns {sorted(expected)}, got {sorted(actual)}"
        _fail(message, raw)

    @staticmethod
    def parse_batch_signals(raw_signals: str | None, expected_turns: int) -> list[dict[str, float]]:
        """Batch format only (BatchSignalSource, covering several turns in
        one call): a header row of signal names, then one
        "<turn>,<value>,..." row per turn (1-based, in the same column
        order as the header), followed by a final BATCH_END_MARKER row
        once every turn has been written. Strict, not lenient: any row
        that isn't a valid "<turn>,<value>,..." row or the marker row
        raises immediately — see MetadataTurnMismatch's own docstring for
        why nothing here is ever silently skipped. The set of turn
        numbers actually present, and the presence of the marker row
        itself, are then checked against expected_turns by
        _turns_in_order."""
        by_turn: dict[int, dict[str, float]] = {}
        terminated = False
        rows = [row for row in csv.reader(io.StringIO(raw_signals or "")) if any(cell.strip() for cell in row)]
        if rows:
            names = [name.strip() for name in rows[0]]
            for row in rows[1:]:
                first_cell = row[0].strip()
                if first_cell.lower() == BATCH_END_MARKER:
                    terminated = True
                    continue
                try:
                    turn = int(first_cell)
                except ValueError:
                    _fail(f"signals: non-numeric turn index -- row: {row}", raw_signals)
                values: dict[str, float] = {}
                for name, raw_value in zip(names, row[1:]):
                    try:
                        values[name] = float(raw_value.strip())
                    except ValueError:
                        _fail(f"signals: non-numeric value for '{name}' -- row: {row}", raw_signals)
                by_turn[turn] = values
        return MetadataHandler._turns_in_order('signals', by_turn, expected_turns, raw_signals, terminated)

    @staticmethod
    def parse_batch_env(raw_env: str | None, expected_turns: int) -> list[dict[str, str]]:
        """Batch format only (BatchSignalSource, covering several turns in
        one call): plain text, not JSON — a "<turn>:" header line per
        turn, in order, followed by that turn's own "key=value" lines
        (zero of them when nothing changed), then a final BATCH_END_MARKER
        line once every turn has been written. Deliberately not one JSON
        blob despite being just as strict: json.loads has no
        partial-recovery mode either, but it also has no way to tell you
        which turn broke — this format is line-based purely for that
        diagnostic, not to tolerate anything json.loads wouldn't. Any
        line that isn't a valid header, a valid "key=value" line, or the
        marker line raises immediately (see MetadataTurnMismatch). The
        set of turn numbers actually present, and the presence of the
        marker line itself, are then checked against expected_turns by
        _turns_in_order."""
        by_turn: dict[int, dict[str, str]] = {}
        terminated = False
        current_turn: int | None = None
        for line in (raw_env or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.lower() == BATCH_END_MARKER:
                terminated = True
                current_turn = None
                continue
            header = stripped[:-1].strip() if stripped.endswith(":") else None
            if header is not None and header.isdigit():
                current_turn = int(header)
                by_turn[current_turn] = {}
                continue
            if current_turn is None:
                _fail(f"env: line outside any turn header -- line: {line!r}", raw_env)
            if "=" not in stripped:
                _fail(f"env: line without '=' -- line: {line!r}", raw_env)
            key, _, value = stripped.partition("=")
            key = key.strip()
            if not key:
                _fail(f"env: line with an empty key -- line: {line!r}", raw_env)
            by_turn[current_turn][key] = value.strip()
        return MetadataHandler._turns_in_order('env', by_turn, expected_turns, raw_env, terminated)
