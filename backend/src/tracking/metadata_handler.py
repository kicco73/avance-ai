from __future__ import annotations

import csv
import io
import json
from typing import Any

from logging_factory import LoggerFactory
from try_again_error import TryAgainError

logger = LoggerFactory.get_logger(__name__)


class MetadataTurnMismatch(TryAgainError):
    """Raised when a parsed batch signals/env value's turn numbers don't
    match the turns that batch call was actually asked to cover — e.g.
    the model treated the whole chat history as turns to fill in,
    instead of just the N it was told to, or the model declared its own
    output complete (a normal stop/finish reason) while the JSON itself
    is objectively incomplete — a case AIServiceProviderOutputTruncatedError
    never catches, since that only fires on the provider's own truncation
    signal, not on whether the content actually parses. Always a model
    mistake, always worth another attempt (TryAgainError): none of these
    failure modes are guaranteed to repeat on a fresh sample. Surfaced
    loudly rather than silently keeping only the expected turns and
    discarding the rest. Batch-only: the single-turn parsers below have
    no turn concept at all, so nothing to mismatch."""
    def __init__(self, kind: str, expected_turns: int, actual: set[int]) -> None:
        expected = set(range(1, expected_turns + 1))
        super().__init__(f"{kind}: expected turns {sorted(expected)}, got {sorted(actual)}")


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
    def _turns_in_order(kind: str, by_turn: dict[int, dict], expected_turns: int, raw: str | None) -> list[dict]:
        """Batch-only check: exactly turns 1..expected_turns must be
        present, no more, no fewer. Returns them as a plain list, index i
        = turn i+1. Logs the raw value on a mismatch — the per-row/per-entry
        error logging in the two parsers below only fires for a row that's
        present but malformed, not for "the model produced nothing usable
        at all", which is exactly what needs the raw text to diagnose."""
        actual = set(by_turn.keys())
        expected = set(range(1, expected_turns + 1))
        if actual != expected:
            logger.error(f"{kind}: expected turns {sorted(expected)}, got {sorted(actual)} -- raw: {raw!r}")
            raise MetadataTurnMismatch(kind, expected_turns, actual)
        return [by_turn[i] for i in range(1, expected_turns + 1)]

    @staticmethod
    def parse_batch_signals(raw_signals: str | None, expected_turns: int) -> list[dict[str, float]]:
        """Batch format only (BatchSignalSource, covering several turns in
        one call): a header row of signal names, then one
        "<turn>,<value>,..." row per turn (1-based, in the same column
        order as the header). Malformed rows are skipped rather than
        raising, since this is model output — but the set of turn numbers
        actually present is always checked against expected_turns and
        raises MetadataTurnMismatch on any mismatch."""
        by_turn: dict[int, dict[str, float]] = {}
        rows = [row for row in csv.reader(io.StringIO(raw_signals or "")) if any(cell.strip() for cell in row)]
        if rows:
            names = [name.strip() for name in rows[0]]
            for row in rows[1:]:
                try:
                    turn = int(row[0].strip())
                except (ValueError, IndexError):
                    logger.error(f"non-numeric turn index -- row: {row}")
                    continue
                values: dict[str, float] = {}
                for name, raw_value in zip(names, row[1:]):
                    try:
                        values[name] = float(raw_value.strip())
                    except ValueError:
                        logger.error(f"non-numeric value for '{name}' -- row: {row}")
                by_turn[turn] = values
        return MetadataHandler._turns_in_order('signals', by_turn, expected_turns, raw_signals)

    @staticmethod
    def parse_batch_env(raw_env: str | None, expected_turns: int) -> list[dict[str, str]]:
        """Batch format only (BatchSignalSource, covering several turns in
        one call): {"<turn>": {"<key>": "<value>", ...}, ...} — one entry
        per turn, present even when nothing changed (mapped to {}) so a
        genuinely missing entry stays distinguishable from "nothing
        changed". Unlike signals' per-row CSV, this is a single JSON blob
        with no per-turn recovery: malformed input degrades to no entries
        at all, since there's no row-by-row structure to salvage a good
        turn from a bad one. Either way, the set of turn numbers actually
        present is always checked against expected_turns and raises
        MetadataTurnMismatch on any mismatch."""
        by_turn: dict[int, dict[str, str]] = {}
        if raw_env:
            try:
                parsed = json.loads(raw_env)
            except Exception as exc:
                logger.error(f"{exc} -- raw env: {raw_env}")
                parsed = None
            if parsed is not None and not isinstance(parsed, dict):
                logger.error(f"expected a JSON object, got {type(parsed).__name__} -- raw env: {raw_env}")
                parsed = None
            if parsed is not None:
                for turn_key, values in parsed.items():
                    try:
                        turn = int(turn_key)
                    except (ValueError, TypeError):
                        logger.error(f"non-numeric turn key {turn_key!r} -- raw env: {raw_env}")
                        continue
                    if not isinstance(values, dict):
                        logger.error(f"turn {turn} value is not an object -- raw env: {raw_env}")
                        continue
                    by_turn[turn] = {str(k): str(v) for k, v in values.items()}
        return MetadataHandler._turns_in_order('env', by_turn, expected_turns, raw_env)
