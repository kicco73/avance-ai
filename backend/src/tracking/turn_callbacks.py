"""Shared callback type aliases for a chat turn's own AI-generation step
— used by both TurnService and every TurnStrategy (see turn_strategy.py),
kept in their own module so neither side needs to import the other just
for these.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

OnChunk = Callable[[str], Awaitable[None]]
OnMetadata = Callable[[str, Any], None]
