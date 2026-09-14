"""The automaton's data model: what a project's index.yml parses into,
and the payload shapes the API serializes those back out as.

Pure data. Nothing here evaluates an expression, analyses text, or needs
a running automaton — which is the point: a compiled automaton builds
these very same objects with literal constructor calls instead of
parsing YAML, so they cannot live beside the class that interprets them
without dragging the interpreter along."""
from __future__ import annotations

from dataclasses import dataclass, field

from typing import ClassVar

from typing_extensions import TypedDict, Literal, Any

class SourceDict(TypedDict):
    type: Literal["text", "base64"]
    media_type: str
    data: str

@dataclass
class MemoryArchive:
    filename: str
    source: SourceDict
@dataclass(frozen=True)
class Action:
    name: str
    ui_label: str
    ui_button: str
    target: str
    ui_description: str | None = None
    trigger: str | None = None
    task: str | None = None
    on_exit: str | None = None
    env: dict[str, str] | None = None
    line: int | None = None

@dataclass(frozen=True)
class State:
    key: str
    ui_label: str
    final: bool
    ui_description: str | None = None
    contextual_prompt: str | None = None
    actions: list[Action] = field(default_factory=list)
    fixed_message: str | None = None
    transition_log_level: str = "WARNING"
    signal_tracking_strategy: str = "relevant"
    attachments: tuple[str, ...] = ()
    history_cutoff: bool = False
    chat_enabled: bool = True
    reactions_enabled: bool = False
    ai_may_read_sources: tuple[str, ...] = ()
    ai_must_read_sources: tuple[str, ...] = ()
    ai_may_write_sources: tuple[str, ...] = ()
    line: int | None = None
    input: tuple[str, ...] = ()
    output: tuple[str, ...] = ()

    @property
    def has_triggerable_actions(self) -> bool:
        return any(a.trigger is not None for a in self.actions)

    @property
    def ai_source_names(self) -> tuple[str, ...]:
        """Every source name this state exposes to the model, for either
        reading or writing — empty means no tool catalog at all."""
        return self.ai_may_read_sources + self.ai_must_read_sources + self.ai_may_write_sources


@dataclass
class Signal:
    MIN_VALUE: ClassVar[int] = 0
    MAX_VALUE: ClassVar[int] = 100

    name: str
    ui_label: str
    definition: str
    attachments: tuple[str, ...] = ()
    ui_description: str | None = None


@dataclass
class Reaction:
    """One project-declared reaction a user or the bot can attach to a
    message — same shape as Signal, same reasoning: `ui_description`
    falls back to `definition` when absent (see AutomatonBuilder._build_reaction)."""
    name: str
    ui_label: str
    definition: str
    ui_description: str | None = None


@dataclass
class EnvKey:
    """One project-level `env:` declaration — the automaton's own variable.
    `value` is the default, evaluated once whenever nothing has set the key
    yet. `ai_definition` is the text the model reads to know what this
    variable means — required for any key some state actually lists in its
    own `input`/`output` (see AutomatonValidator.validate_state_io), unused
    otherwise. Whether the model sees or produces a given key at all is
    decided per state, by that state's own `input`/`output` (see
    automaton.State) — never a property of the key itself. Scripts (an
    action's own `env:`) write any key regardless."""
    name: str
    value: str = ""
    ui_description: str | None = None
    ai_definition: str | None = None


@dataclass
class Source:
    """One `sources:` declaration — binds `name` (what `source.<name>.*`
    calls in a trigger/env: expression resolve against, see
    tracking.sources.SourceNamespace) to a driver and its own target,
    both encoded in `url` (`<scheme>:<path>`, e.g.
    'avance:behaviour/flights.csv' — see tracking.sources.url)."""
    name: str
    url: str
    ui_label: str
    ui_description: str | None = None
    ai_definition: str | None = None

ActionPayload = TypedDict("ActionPayload", {
    "name": str,
    "ui_label": str,
    "ui_button": str,
    "ui_description": str | None,
    "target": str,
    "has_trigger": bool,
    "task": str | None,
    "on-exit": str | None,
})

class ReactionOptionPayload(TypedDict):
    key: str
    ui_label: str

class StatePayload(TypedDict):
    key: str
    ui_label: str
    ui_description: str | None
    final: bool
    chat_enabled: bool
    reactions: list[ReactionOptionPayload]
    actions: list[ActionPayload]
    ai_may_read_sources: list[str]
    ai_must_read_sources: list[str]
    ai_may_write_sources: list[str]
    input: list[str]
    output: list[str]

class SignalPayload(TypedDict):
    name: str
    ui_label: str | None
    ui_description: str | None
    definition: str
    attachments: dict[str, MemoryArchive]
    error: bool | None

class EnvKeyPayload(TypedDict):
    name: str
    ui_description: str | None
    value: str
    ai_definition: str | None

class SourcePayload(TypedDict):
    name: str
    ui_label: str
    ui_description: str | None
    ai_definition: str | None
    url: str

class ProjectPayload(TypedDict):
    id: str
    family: str | None
    revision: int
    ui_label: str | None
    ui_description: str | None
    services: dict[str, str]
    signal_tracking_on_ai_message: bool
    new_session_strategy: str
    general_prompt: str
