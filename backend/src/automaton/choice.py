from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

CHOICE_BUTTON_PREFIX = "choice:"
PROFILE_FIELDS = ("title", "description", "key")
PROFILE_OPTIONAL_FIELDS = ("picture_url",)


@dataclass(frozen=True)
class ChoiceSelection:
    key: str
    option: str

    NONE: ClassVar["ChoiceSelection"]


ChoiceSelection.NONE = ChoiceSelection(key="", option="")


class PlainOption:
    def __init__(self, text: str) -> None:
        self._text = text

    @property
    def value(self) -> str:
        return self._text

    def translatable_texts(self) -> tuple[str, ...]:
        return (self._text,)

    def button_fields(self, translations: dict[str, str]) -> dict:
        label = translations.get(self._text, self._text)
        return {"ui_label": label, "ui_button": label}


class ProfileOption:
    def __init__(self, profile: dict) -> None:
        self._profile = profile

    @property
    def value(self) -> str:
        return self._profile["key"]

    def translatable_texts(self) -> tuple[str, ...]:
        return (self._profile["title"], self._profile["description"])

    def button_fields(self, translations: dict[str, str]) -> dict:
        title = translations.get(self._profile["title"], self._profile["title"])
        return {
            "ui_label": title,
            "ui_button": title,
            "profile": {
                **self._profile,
                "title": title,
                "description": translations.get(self._profile["description"], self._profile["description"]),
            },
        }


def is_profile(option: object) -> bool:
    return (
        isinstance(option, dict)
        and set(PROFILE_FIELDS) <= set(option) <= set(PROFILE_FIELDS + PROFILE_OPTIONAL_FIELDS)
        and all(isinstance(text, str) for text in option.values())
    )


def is_option_list(value: object) -> bool:
    if not isinstance(value, list):
        return False
    return all(isinstance(option, str) for option in value) or all(is_profile(option) for option in value)


def option_of(raw: str | dict) -> PlainOption | ProfileOption:
    return {True: ProfileOption, False: PlainOption}[isinstance(raw, dict)](raw)


def option_value(raw: str | dict) -> str:
    return option_of(raw).value


def button_name(key: str, index: int) -> str:
    return f"{CHOICE_BUTTON_PREFIX}{key}:{index}"


def parse_button_name(name: str, options_by_key: dict[str, list]) -> ChoiceSelection | None:
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
    return ChoiceSelection(key=key, option=option_value(options[index]))
