from __future__ import annotations

from typing import Any

from automaton.model import ENV_TYPE_DEFAULTS


class EnvType:
    name: str

    def accepts(self, value: Any) -> bool:
        raise NotImplementedError

    def accepts_kind(self, kind: str) -> bool:
        return kind == self.name

    def default_literal(self) -> str:
        return repr(ENV_TYPE_DEFAULTS[self.name])


class NumberType(EnvType):
    name = "number"

    def accepts(self, value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)


class StringType(EnvType):
    name = "string"

    def accepts(self, value: Any) -> bool:
        return isinstance(value, str)


class BoolType(EnvType):
    name = "bool"

    def accepts(self, value: Any) -> bool:
        return isinstance(value, bool)


class ChoiceType(EnvType):
    name = "choice"

    def accepts(self, value: Any) -> bool:
        return isinstance(value, list) and all(isinstance(option, str) for option in value)


class UndeclaredType(EnvType):
    name = "undeclared"

    def accepts(self, value: Any) -> bool:
        return True

    def accepts_kind(self, kind: str) -> bool:
        return True


ENV_TYPES: dict[str, EnvType] = {
    env_type.name: env_type for env_type in (NumberType(), StringType(), BoolType(), ChoiceType())
}
UNDECLARED_ENV_TYPE = UndeclaredType()
ENV_TYPE_NAMES = ", ".join(ENV_TYPES)
STORED_ENV_TYPES: dict[str, EnvType] = {**ENV_TYPES, UNDECLARED_ENV_TYPE.name: UNDECLARED_ENV_TYPE}
