"""One callable a provider's own native tool-calling exposes to the
model this turn — see tracking.sources.ToolSet.specs(), the only
producer of these today. `name` is always "source_<source name>_<method>";
`parameters` is a JSON Schema object — the method's uniform schema
(tracking.sources.METHOD_SCHEMAS: `values` an array of strings, plus
`keys`/`fields`), possibly narrowed by the driver with enums, fixed
object properties or descriptions (see SourceDriver.parameter_schema).
Every provider forwards it as-is except Gemini, which translates it to
its own Schema dialect.

Lives outside both `ai/` and `tracking/`, the way token_estimate.py and
content_text.py do: it's a plain data shape both sides need — built by
core (tracking.sources.ToolSet), consumed by the ai skill's providers —
with no LLM behavior of its own."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
