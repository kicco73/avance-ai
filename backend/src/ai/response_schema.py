from __future__ import annotations

import json
from typing import Any

from system.logging_factory import LoggerFactory

logger = LoggerFactory.get_logger(__name__)


class Field:
	json_type = ""

	def __init__(self, description: str = "") -> None:
		self.description = description

	def json_schema(self) -> dict[str, Any]:
		return {"type": self.json_type, **self._described()}

	def coerce(self, value: Any) -> Any:
		return value

	def nullable(self) -> "Field":
		return NullableField(self)

	def as_output_field(self) -> "Field":
		return self.nullable()

	def _described(self) -> dict[str, str]:
		return {"description": self.description} if self.description else {}


class StringField(Field):
	json_type = "string"

	def coerce(self, value: Any) -> str:
		return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


class NumberField(Field):
	json_type = "number"

	def coerce(self, value: Any) -> float:
		return float(value)


class BooleanField(Field):
	json_type = "boolean"

	def coerce(self, value: Any) -> bool:
		return value if isinstance(value, bool) else str(value).strip().lower() == "true"

	def as_output_field(self) -> "Field":
		return self


class ArrayField(Field):
	json_type = "array"

	def __init__(self, items: Field, description: str = "") -> None:
		super().__init__(description)
		self.items = items

	def json_schema(self) -> dict[str, Any]:
		return {**super().json_schema(), "items": self.items.json_schema()}

	def coerce(self, value: Any) -> list[Any]:
		if not isinstance(value, list):
			raise ValueError("not an array")
		return [self.items.coerce(item) for item in value]


class ObjectField(Field):
	json_type = "object"

	def __init__(self, fields: dict[str, Field], description: str = "") -> None:
		super().__init__(description)
		self.fields = fields

	def json_schema(self) -> dict[str, Any]:
		return {
			**super().json_schema(),
			"properties": {name: field.json_schema() for name, field in self.fields.items()},
			"required": list(self.fields),
			"additionalProperties": False,
		}

	def coerce(self, value: Any) -> dict[str, Any]:
		coerced: dict[str, Any] = {}
		if not isinstance(value, dict):
			logger.error(f"not an object, dropped -- raw: {value!r}")
			return coerced
		for name, raw in value.items():
			try:
				coerced[name] = self.fields.get(name, Field()).coerce(raw)
			except (TypeError, ValueError) as exc:
				logger.error(f"dropping '{name}': {exc} -- raw: {raw!r}")
		return coerced


class NullableField(Field):
	def __init__(self, inner: Field) -> None:
		super().__init__(inner.description)
		self.inner = inner

	def json_schema(self) -> dict[str, Any]:
		inner = {key: value for key, value in self.inner.json_schema().items() if key != "description"}
		return {"anyOf": [inner, {"type": "null"}], **self._described()}

	def coerce(self, value: Any) -> Any:
		return None if value is None else self.inner.coerce(value)

	def nullable(self) -> "Field":
		return self
