from __future__ import annotations

import re
from collections.abc import MutableMapping, Sequence
from typing import ClassVar, Self

from automaton.builder.build_cursor import BuildCursor
from automaton.identifier_registry import IdentifierRegistry
from automaton.project_services import DISABLED, REQUIRED

SCRIPT_FIELDS = ("task", "on-exit", "on-enter", "actuator")
LEGACY_NAMESPACE = "actuator"
NAMESPACES = {
    **{name: "task" for name in IdentifierRegistry.TASK},
    **{name: "chat" for name in IdentifierRegistry.CHAT},
}
_CALL = re.compile(r"\b" + LEGACY_NAMESPACE + r"\.([A-Za-z_][A-Za-z0-9_]*)")


def _mapping(value) -> MutableMapping:
    return value if isinstance(value, MutableMapping) else {}


def _sequence(value) -> list:
    return value if isinstance(value, list) else []


def actions_in(raw) -> list[MutableMapping]:
    """Every mapping a deprecated action field can physically live in.
    A top-level `actions:` is not read by the builder, but a project can
    keep its anchors there and merge them into states (`<<: *results`),
    and that anchor is where the field actually is — rewriting the state
    that merges it would write a copy and leave the original to be
    merged in again on the next build."""
    document = _mapping(raw)
    nested = [
        action
        for state in _mapping(document.get("states")).values()
        for action in _sequence(_mapping(state).get("actions"))
    ]
    anchored = _sequence(document.get("actions"))
    return [
        _mapping(action)
        for action in [document.get("init-action"), *anchored, *nested]
        if _mapping(action)
    ]


def actions_targeting(raw, state_name: str) -> list[MutableMapping]:
    """Every action that lands the conversation in `state_name` — a
    missing `target` is a self-loop, so it counts as reaching its own
    state, and the init-action reaches whatever it starts on."""
    document = _mapping(raw)
    init_action = _mapping(document.get("init-action"))
    reached = [init_action] if init_action.get("target") == state_name else []
    return reached + [
        _mapping(action)
        for key, state in _mapping(document.get("states")).items()
        for action in _sequence(_mapping(state).get("actions"))
        if _mapping(action).get("target", key) == state_name
    ]


def named_entries_in(raw, section: str) -> list[tuple[str, MutableMapping]]:
    return [
        (name, _mapping(entry))
        for name, entry in _mapping(_mapping(raw).get(section)).items()
        if _mapping(entry)
    ]


def own_field(action: MutableMapping, field: str):
    """What this action itself says, never what a merge key supplies.
    Writing to a mapping only ever writes locally, so a merged key read
    as if it were the action's own turns one rewrite into a copy that
    shadows the original — and the original keeps being merged in, so
    every later run finds it again and appends its rewrite once more."""
    items = getattr(action, "non_merged_items", None)
    if items is None:
        return action.get(field)
    return next((value for key, value in items() if key == field), None)


def owns(action: MutableMapping, field: str) -> bool:
    items = getattr(action, "non_merged_items", None)
    return field in (dict(items()) if items is not None else action)


def _lines_of(action: MutableMapping, field: str) -> list[str]:
    script = own_field(action, field)
    return [line for line in (script if isinstance(script, str) else "").splitlines() if line.strip()]


def _text_of(action: MutableMapping, field: str) -> str:
    text = own_field(action, field)
    return text.strip() if isinstance(text, str) else ""


def _store(editor, action, field: str, script: str) -> None:
    if not script:
        if owns(action, field):
            editor.drop_key_preserving_comments(action, field)
        return
    if script != own_field(action, field):
        action[field] = script


def _rewritable(line: str) -> list[str]:
    calls = _CALL.findall(line)
    known = [call for call in calls if call in NAMESPACES]
    targets = {NAMESPACES[call] for call in known}
    return known if len(known) == len(calls) and len(targets) < 2 else []


def rewritable(action: MutableMapping) -> bool:
    return all(
        _rewritable(line) or not _CALL.search(line)
        for field in SCRIPT_FIELDS
        for line in _lines_of(action, field)
    )


class Deprecation:
    """One spelling the format has moved past, and the rewrite that
    settles it. Nothing in a build knows these exist — a build reads the
    fields it reads and refuses every other, and this is what the
    modernizer consults before a build ever sees the file."""

    line: int | None = None

    @property
    def section(self) -> str:
        return "project"


class LegacyTalkEnabled(Deprecation):

    KEY = "talk-enabled"
    LEVELS = {True: REQUIRED, False: DISABLED}

    def __init__(self, level: str, line: int | None) -> None:
        self.level = level
        self.line = line

    @classmethod
    def found_in(cls, raw) -> list["LegacyTalkEnabled"]:
        project = _mapping(_mapping(raw).get("project"))
        return [
            cls(cls.LEVELS[bool(value)], BuildCursor.line_of(project, cls.KEY))
            for value in [project.get(cls.KEY)]
            if isinstance(value, bool)
        ]

    @property
    def message(self) -> str:
        return f"project.{self.KEY} is deprecated — write '{self.spelling}' instead."

    @property
    def fix(self) -> str:
        return f"project.{self.KEY} → {self.spelling}"

    @property
    def spelling(self) -> str:
        return f"services: {{talk: {self.level}}}"

    def rewrite(self, editor) -> None:
        editor.set_service_level("talk", self.level)


class EntryDeprecation(Deprecation):
    """A key deprecated wherever an entry of one section carries it. The
    subclass says which section the entries come from; `rewrite` re-reads
    them off the editor's own tree rather than holding the nodes
    detection saw, since the two are the same document parsed twice."""

    SECTION: ClassVar[str]
    KEY: ClassVar[str]

    def __init__(self, name: str, line: int | None) -> None:
        self.name = name
        self.line = line

    @property
    def section(self) -> str:
        return f"{self.SECTION}.{self.name}"

    @classmethod
    def entries(cls, raw) -> list[tuple[str, MutableMapping]]:
        raise NotImplementedError

    @classmethod
    def found_in(cls, raw) -> Sequence[Self]:
        return [
            cls(name, BuildCursor.line_of(entry, cls.KEY))
            for name, entry in cls.entries(raw)
            if owns(entry, cls.KEY)
        ]

    def mine(self, editor) -> list[MutableMapping]:
        return [entry for _, entry in self.entries(editor.document()) if owns(entry, self.KEY)]


class ActionDeprecation(EntryDeprecation):

    SECTION = "actions"

    @classmethod
    def entries(cls, raw) -> list[tuple[str, MutableMapping]]:
        return [(cls._name_of(action), action) for action in actions_in(raw)]

    @staticmethod
    def _name_of(action: MutableMapping) -> str:
        name = action.get("name")
        return name if isinstance(name, str) else "init-action"


class StateDeprecation(EntryDeprecation):

    SECTION = "states"

    @classmethod
    def entries(cls, raw) -> list[tuple[str, MutableMapping]]:
        return named_entries_in(raw, "states")


class EnvKeyDeprecation(EntryDeprecation):

    SECTION = "env"

    @classmethod
    def entries(cls, raw) -> list[tuple[str, MutableMapping]]:
        return named_entries_in(raw, "env")


class LegacyScriptField(ActionDeprecation):

    FIELD = "task"

    @classmethod
    def found_in(cls, raw) -> Sequence[Self]:
        return [found for found in super().found_in(raw) if found._settled(raw)]

    def _settled(self, raw) -> bool:
        return all(rewritable(entry) for name, entry in self.entries(raw) if name == self.name)

    @property
    def fix(self) -> str:
        return f"{self.name}: {self.KEY} → {self.FIELD}"

    def rewrite(self, editor) -> None:
        for action in [action for action in self.mine(editor) if rewritable(action)]:
            script = "\n".join(_lines_of(action, self.FIELD) + _lines_of(action, self.KEY))
            editor.rename_key_preserving_comments(action, self.KEY, self.FIELD)
            _store(editor, action, self.FIELD, script)


class LegacyOnEnter(LegacyScriptField):

    KEY = "on-enter"
    MESSAGE = (
        "Action '{name}': 'on-enter' is deprecated — the field is called 'task' now, "
        "and what it says is ignored until it is renamed."
    )


class LegacyActuatorField(LegacyScriptField):

    KEY = "actuator"
    MESSAGE = (
        "Action '{name}': 'actuator' is deprecated — the field is called 'task' now, "
        "and what it says is ignored until it is renamed."
    )


class LegacyActionPrompt(LegacyScriptField):

    KEY = "action-prompt"
    CALL = "task.prompt"
    MESSAGE = (
        "Action '{name}': 'action-prompt' is deprecated — a prompt is a "
        "task.prompt(...) call in 'task' now, and what it says is ignored until it is one."
    )

    @property
    def fix(self) -> str:
        return f"{self.name}: {self.KEY} → {self.CALL}(...)"

    def rewrite(self, editor) -> None:
        for action in [action for action in self.mine(editor) if rewritable(action)]:
            script = _lines_of(action, self.FIELD)
            prompt = _text_of(action, self.KEY)
            call = [f"{self.CALL}({prompt!r})" for _ in [prompt] if prompt]
            editor.rename_key_preserving_comments(action, self.KEY, self.FIELD)
            _store(editor, action, self.FIELD, "\n".join(script + call))


class LegacyActuatorCall(ActionDeprecation):

    FIELD_PREFIXES = {"chat.": "on-exit", "env.": "on-exit", "task.": "task"}

    def __init__(self, name: str, call: str, line: int | None) -> None:
        super().__init__(name, line)
        self.call = call

    @property
    def fix(self) -> str:
        return f"{self.name}: {LEGACY_NAMESPACE}.{self.call} → {self.namespace}.{self.call}"

    @property
    def namespace(self) -> str:
        return NAMESPACES[self.call]

    @classmethod
    def found_in(cls, raw) -> Sequence[Self]:
        return [
            cls(name, call, BuildCursor.own_line(action))
            for name, action in cls.entries(raw)
            if rewritable(action)
            for call in cls._calls_in(action)
        ]

    def rewrite(self, editor) -> None:
        for name, action in self.entries(editor.document()):
            if name == self.name and rewritable(action) and self._calls_in(action):
                self._redistribute(editor, action)

    @staticmethod
    def _calls_in(action: MutableMapping) -> list[str]:
        found: list[str] = []
        for field in SCRIPT_FIELDS:
            for line in _lines_of(action, field):
                found += [call for call in _rewritable(line) if call not in found]
        return found

    def _redistribute(self, editor, action) -> None:
        scripts: dict[str, list[str]] = {"task": [], "on-exit": []}
        for field in ("task", "on-exit"):
            for line in _lines_of(action, field):
                rewritten = self._rewritten(line)
                scripts[self._field_of(rewritten, field)].append(rewritten)
        for field, script in scripts.items():
            _store(editor, action, field, "\n".join(script))

    @staticmethod
    def _rewritten(line: str) -> str:
        for call in _rewritable(line):
            line = line.replace(f"{LEGACY_NAMESPACE}.{call}", f"{NAMESPACES[call]}.{call}")
        return line

    @classmethod
    def _field_of(cls, line: str, current: str) -> str:
        return next(
            (field for prefix, field in cls.FIELD_PREFIXES.items() if line.lstrip().startswith(prefix)),
            current,
        )


class RenamedKey(EntryDeprecation):
    """The whole deprecation: this key is that key now, and nothing else
    about the entry changes."""

    NEW: ClassVar[str]

    @property
    def fix(self) -> str:
        return f"{self.name}: {self.KEY} → {self.NEW}"

    def rewrite(self, editor) -> None:
        for entry in self.mine(editor):
            editor.rename_key_preserving_comments(entry, self.KEY, self.NEW)


class RemovedKey(EntryDeprecation):
    """A key the format dropped without putting anything in its place,
    so there is nothing to carry over — it has not been read since it was
    removed, and deleting it is the only thing that can be said
    mechanically. Each one names itself: nothing removes a key it was not
    told about, or a typo would be silently deleted instead of reported."""

    @property
    def fix(self) -> str:
        return f"{self.name}: {self.KEY} removed"

    def rewrite(self, editor) -> None:
        for entry in self.mine(editor):
            editor.drop_key_preserving_comments(entry, self.KEY)


class LegacyStateScript(StateDeprecation):
    """A state used to carry a script of its own, run on entering it.
    Today that is the entering action's business, and an action is
    reached by knowing which ones target the state — so the script goes
    to each of them, which is once per entry either way. A state nothing
    reaches has nowhere to put it, and keeps the warning."""

    KEY = "on-enter"
    FIELD = "task"
    MESSAGE = (
        "State '{name}': 'on-enter' is deprecated — a state has no script of its own, and "
        "what it says runs nowhere until it moves to the 'task' of the actions that reach it."
    )

    @classmethod
    def found_in(cls, raw) -> Sequence[Self]:
        return [found for found in super().found_in(raw) if found._movable(raw)]

    def _movable(self, raw) -> bool:
        reached = actions_targeting(raw, self.name)
        return bool(reached) and all(rewritable(action) for action in reached)

    @property
    def fix(self) -> str:
        return f"{self.name}: {self.KEY} → {self.FIELD} of the actions that reach it"

    def rewrite(self, editor) -> None:
        raw = editor.document()
        for name, state in self.entries(raw):
            if name != self.name or not owns(state, self.KEY):
                continue
            script = _lines_of(state, self.KEY)
            for action in actions_targeting(raw, name):
                _store(editor, action, self.FIELD, "\n".join(_lines_of(action, self.FIELD) + script))
            editor.drop_key_preserving_comments(state, self.KEY)


class LegacyStateChat(StateDeprecation, RenamedKey):

    KEY = "chat"
    NEW = "chat-enabled"
    MESSAGE = (
        "State '{name}': 'chat' is deprecated — the field is called 'chat-enabled' now, "
        "and the state stays open to chat until it is renamed."
    )


class LegacyAiMemoryStrategy(StateDeprecation, RenamedKey):

    KEY = "ai-memory-strategy"
    NEW = "ai-memory-scope"
    VALUES = {"keep": "global", "clear": "local"}
    MESSAGE = (
        "State '{name}': 'ai-memory-strategy' is deprecated — the field is called "
        "'ai-memory-scope' now, with 'keep'/'clear' renamed to 'global'/'local', and "
        "what it says is ignored until it is renamed."
    )

    def rewrite(self, editor) -> None:
        for entry in self.mine(editor):
            value = own_field(entry, self.KEY)
            editor.rename_key_preserving_comments(entry, self.KEY, self.NEW)
            entry[self.NEW] = self.VALUES.get(value, value) if isinstance(value, str) else value


class RemovedEnvAiAccess(EnvKeyDeprecation, RemovedKey):

    KEY = "ai-access"
    MESSAGE = (
        "Env key '{name}': 'ai-access' was removed — what the model may read or set is "
        "each state's own 'input'/'output' now, never a property of the key."
    )


class RemovedEnvUiLabel(EnvKeyDeprecation, RemovedKey):

    KEY = "ui-label"
    MESSAGE = (
        "Env key '{name}': 'ui-label' was removed — an env key is shown under its own "
        "name, and nothing has read this since."
    )


PROJECT_KINDS = (LegacyTalkEnabled,)
FIELD_KINDS = (
    LegacyOnEnter, LegacyActuatorField, LegacyActionPrompt, LegacyStateScript,
    LegacyStateChat, LegacyAiMemoryStrategy, RemovedEnvAiAccess, RemovedEnvUiLabel,
)
KINDS = PROJECT_KINDS + FIELD_KINDS + (LegacyActuatorCall,)


def found_in(raw) -> list[Deprecation]:
    return [found for kind in KINDS for found in kind.found_in(raw)]
