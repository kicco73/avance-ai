"""The automaton's data model: what a project's index.yml parses into,
and the payload shapes the API serializes those back out as.

Pure data. Nothing here evaluates an expression, analyses text, or needs
a running automaton — which is the point: a compiled automaton builds
these very same objects with literal constructor calls instead of
parsing YAML, so they cannot live beside the class that interprets them
without dragging the interpreter along."""
from __future__ import annotations

from dataclasses import dataclass, field

from typing_extensions import TypedDict, Literal, Any

class SourceDict(TypedDict):
    type: Literal["text", "base64"]
    media_type: str
    data: str

@dataclass
class MemoryArchive:
    filename: str
    source: SourceDict

@dataclass
class Action:
    name: str
    ui_label: str
    ui_button: str
    target: str
    ui_description: str | None = None
    trigger: str | None = None
    attachments: dict[str, MemoryArchive] = field(default_factory=dict[str, MemoryArchive])
    # Not state-level: two different actions landing on the same target
    # state can each carry their own value (or none), since it describes
    # *how you got there*, not the destination itself.
    task: str | None = None
    # Same statement-splitting as task (TriggerExpressionAnalyzer.
    # task_statements): a mix of `env.<key> = expr` lines
    # (TriggerExpressionAnalyzer.on_exit_assignment) and bare
    # `chat.<method>(...)` calls — no task.*'s own send_mail/whatsapp/
    # defer/prompt, that stays task's job. The future replacement for
    # the declarative `env:` map below (its own env-write half): see
    # Automaton.eval_action_on_exit.
    on_exit: str | None = None
    # {env key: expression source}, evaluated when this action fires and
    # merged onto the env store so the next prompt sees the update. Same
    # scope/mechanics as `trigger` (see _eval_trigger), minus the boolean
    # cast. Legacy authoring path, kept working for already-published
    # YAML — new actions declare the same writes as `on-exit` lines instead.
    env: dict[str, str] | None = None
    # 0-based line in the project's own index.yml where this action is
    # declared (see AutomatonBuilder._build_action) — None for a
    # synthetic action with no YAML origin of its own. Matches
    # CodeEditor.vue's own jumpToLine convention; carried so a build
    # error raised well after the original YAML node is gone
    # (_actions_sanity_check, long past Pass 1) can still report where
    # it happened (see AutomatonBuildError).
    line: int | None = None

@dataclass
class State:
    key: str
    ui_label: str
    # Derived at load time as `len(actions) == 0`, not read from YAML —
    # structurally impossible to desync from the actual actions list.
    final: bool
    ui_description: str | None = None
    # Required unless fixed_message is set — the two are mutually exclusive
    # (see AutomatonBuilder.build): a fixed_message state never generates
    # free-form content, so it has no use for one.
    contextual_prompt: str | None = None
    actions: list[Action] = field(default_factory=list)
    # If set, the state doesn't generate free-form replies: the caller must
    # return this message (translated into the user's language) as-is.
    fixed_message: str | None = None
    # Log level (name) used when logging a transition landing on this state.
    transition_log_level: str = "WARNING"
    attachments: dict[str, MemoryArchive] = field(default_factory=dict[str, MemoryArchive])
    # If true, messages from before the transition into this state are kept
    # out of both the AI reply and auto-tracking's signal evaluation.
    history_cutoff: bool = False
    # If false, chat turns are rejected while this is the current state
    # (see chat.turn_processor.TurnProcessor._begin_turn) — independent of
    # fixed_message/history_cutoff: neither implies this. Named
    # chat_enabled, not chat, to keep clear of the unrelated `chat.*`
    # expression namespace an on-exit script can call into (see
    # tracking.actuators.chat_namespace).
    chat_enabled: bool = True
    # If true, the bot may react to the user's message this turn, choosing
    # from the project's whole `reactions` dict — never a per-state subset
    # (see TurnProtocol's own conditional inclusion of the 'reaction' tag).
    reactions_enabled: bool = False
    # Names of this project's own `sources:` whose reads the model may
    # call as a native tool while replying in this state (see
    # tracking.sources.ToolSet) — every name already validated at build
    # time against `sources:` (AutomatonBuilder's own sanity check), and
    # each one's own source required to carry an `ai-definition` (see
    # Source.ai_definition). The model decides for itself whether/when to
    # call one of these.
    ai_may_read_sources: tuple[str, ...] = ()
    # Same validation as ai_may_read_sources, but forced once per entry
    # into this state (see TrackingProcessor.force_required_tools_for):
    # the first tool-call round after a transition lands here restricts
    # the model to calling one of *these* reads — never both read
    # fields at once for the same source name (AutomatonBuilder rejects
    # that overlap).
    ai_must_read_sources: tuple[str, ...] = ()
    # Names of this project's own `sources:` whose `update` the model may
    # call here — only a source whose driver actually supports update
    # (today, just `avance:env`, see tracking.sources.avance_env) may be
    # listed, checked at build time. A write is never forced: there is no
    # must-write counterpart.
    ai_may_write_sources: tuple[str, ...] = ()
    # Empty for all three fields means no tool catalog at all this turn —
    # TrackingProcessor passes tool_set=None then, the same request shape
    # a turn always sent before tool-calling existed.
    # Same convention as Action.line above — None for the synthetic ""
    # pseudo-state.
    line: int | None = None
    # Names of this automaton's own declared `env:` variables (see EnvKey)
    # this state receives as input — what the model reads about the world
    # before it replies. Every name here must also be declared in `env:`
    # (checked at build time, see AutomatonValidator.validate_state_io).
    input: tuple[str, ...] = ()
    # Names of this automaton's own declared `env:` variables this state
    # produces — the model fills each one in as part of its own structured
    # reply (see tracking.prompt.OutputPrompt), and the resulting values are
    # copied onto the real env keys automatically once the turn completes
    # (see TrackingProcessor.process). Same existence requirement as `input`.
    output: tuple[str, ...] = ()

    @property
    def has_triggerable_actions(self) -> bool:
        return any(a.trigger is not None for a in self.actions)

    @property
    def ai_source_names(self) -> tuple[str, ...]:
        """Every source name this state exposes to the model, for either
        reading or writing — empty means no tool catalog at all."""
        return self.ai_may_read_sources + self.ai_must_read_sources + self.ai_may_write_sources

    @property
    def ai_read_source_names(self) -> tuple[str, ...]:
        return self.ai_may_read_sources + self.ai_must_read_sources


@dataclass
class Signal:
    name: str
    ui_label: str
    definition: str
    # Attachments for this signal's definition, sent only with the signals
    # computation call (never with normal chat turns).
    attachments: dict[str, MemoryArchive] = field(default_factory=dict[str, MemoryArchive])
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
    # Text written *for the model*, never the UI (see ui_description
    # above, which is for the human) — becomes part of the tool's own
    # description whenever this source is exposed as a native tool (see
    # tracking.sources.ToolSet). Required (build error otherwise) for any
    # source named in a state's own ai-may-read-sources/
    # ai-must-read-sources/ai-may-write-sources — same requirement a
    # signal's own `definition` gets — optional for every other source.
    ai_definition: str | None = None


# Functional syntax (not the class form the other Payload types use):
# "task" isn't a valid Python identifier, so a class body can't declare it.
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
    # The project's whole reaction vocabulary, independent of `key` — a
    # user can react with any of these on any bot message, regardless of
    # which state produced it. See State.reactions_enabled for the bot's
    # own, per-state gated side of this.
    reactions: list[ReactionOptionPayload]
    actions: list[ActionPayload]
    # Names of this project's own `sources:` this state exposes to the
    # model as native tools — see State.ai_may_read_sources/
    # ai_must_read_sources/ai_may_write_sources.
    ai_may_read_sources: list[str]
    ai_must_read_sources: list[str]
    ai_may_write_sources: list[str]
    # Names of this automaton's own declared `env:` variables this state
    # reads/produces — see State.input/State.output.
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
    talk_enabled: bool
    signal_tracking_on_ai_message: bool
    general_prompt: str
