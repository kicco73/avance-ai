"""Deliberately pessimistic, network-free token estimate — chars // 3
stands in for a real tokenizer (which usually runs closer to chars // 4),
so this always errs toward overestimating a request's real size rather
than under-guessing it. Shared by tracking's own input-token-budget-per-
turn cap (tracking.turn_size_estimate) and AiService's own tool-loop
round of the same cap, which is why it lives at the top level rather than
inside either package.
"""
from __future__ import annotations

CHARS_PER_TOKEN_ESTIMATE = 3


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN_ESTIMATE
