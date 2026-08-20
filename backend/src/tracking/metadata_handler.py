from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

class MetadataHandler(object):
    @staticmethod
    def parse_raw_signals(raw_signals: str) -> dict[str,float]:
        signals: dict[str, Any] = {}
        if not raw_signals:
            return signals
        try:
            signals = json.loads(raw_signals) or {}
            assert isinstance(signals, dict)
        except Exception as exc:
            logger.warning(f"parse_raw_signals(): {exc}")
        return signals

    @staticmethod
    def parse_raw_env(raw_env: str) -> dict[str, str]:
        """One "key: value" pair per line, optionally prefixed with "-";
        blank lines and anything without a ':' are ignored rather than
        raising — deliberately forgiving, since this is model output."""
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
