"""Shared callback type aliases for a chat turn's own AI-generation step
— used by both ChatService and every TurnStrategy (see turn_strategy.py),
kept in their own module so neither side needs to import the other just
for these.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

OnChunk = Callable[[str], Awaitable[None]]
# Synchronous, never awaited — called at most once per metadata key
# ("audio", "signals", "env", ...) a turn's own reply carries, in whatever
# order the active TurnStrategy resolves them; every event reaches its
# consumer in the exact order it was raised, so a "done" queued right
# after the turn returns can never overtake a chunk.
OnMetadata = Callable[[str, Any], None]
