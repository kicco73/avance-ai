from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

CHOICE_BUTTON_PREFIX = "choice:"


@dataclass(frozen=True)
class ChoiceSelection:
    key: str
    option: str

    NONE: ClassVar["ChoiceSelection"]


ChoiceSelection.NONE = ChoiceSelection(key="", option="")


def button_name(key: str, index: int) -> str:
    return f"{CHOICE_BUTTON_PREFIX}{key}:{index}"


def parse_button_name(name: str, options_by_key: dict[str, list[str]]) -> ChoiceSelection | None:
    if not name.startswith(CHOICE_BUTTON_PREFIX):
        return None
    key, _, index_text = name[len(CHOICE_BUTTON_PREFIX):].rpartition(":")
    options = options_by_key.get(key)
    if options is None:
        raise ValueError(f"'{name}': no choice key '{key}' is offered here.")
    try:
        index = int(index_text)
    except ValueError as exc:
        raise ValueError(f"'{name}': '{index_text}' is not an option index.") from exc
    if not 0 <= index < len(options):
        raise ValueError(f"'{name}': option {index} is out of range — '{key}' has {len(options)} option(s).")
    return ChoiceSelection(key=key, option=options[index])
