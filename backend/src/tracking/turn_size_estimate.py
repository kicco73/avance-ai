from __future__ import annotations

from dataclasses import dataclass

from automaton.automaton import MemoryArchive
from token_estimate import estimate_tokens
from tracking.env import Env


@dataclass(frozen=True)
class SizeEntry:
    kind: str
    label: str
    tokens: int


@dataclass(frozen=True)
class TurnSizeEstimate:
    entries: tuple[SizeEntry, ...]

    @property
    def total_tokens(self) -> int:
        return sum(entry.tokens for entry in self.entries)

    def heaviest(self, count: int = 3) -> list[SizeEntry]:
        return sorted(self.entries, key=lambda entry: entry.tokens, reverse=True)[:count]


def estimate_turn_request(
    base_prompt: str, signal_definition: str | None, reaction_definition: str | None,
    env: Env, attachments: list[MemoryArchive],
) -> TurnSizeEstimate:
    prompt_text = base_prompt + (signal_definition or "") + (reaction_definition or "")
    entries = [SizeEntry("prompt", "prompt", estimate_tokens(prompt_text))]
    for key, value in {**env.stored(), **env.action_set()}.items():
        entries.append(SizeEntry("env", key, estimate_tokens(f"{key}: {value}")))
    for attachment in attachments:
        data = attachment.source.get("data") or ""
        entries.append(SizeEntry("attachment", attachment.filename, estimate_tokens(data)))
    return TurnSizeEstimate(tuple(entries))
