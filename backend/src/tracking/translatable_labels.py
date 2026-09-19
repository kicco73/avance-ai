from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TranslatableLabels:
    state_key: str
    session_id: int
    items: list[tuple[str, str]] = field(default_factory=list)

    def contribute(self, key: str, text: str) -> None:
        if text:
            self.items.append((key, text))
