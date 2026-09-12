"""The Automaton the platform runs on: CoreAutomaton plus every platform
contract, composed.

The split behind this one line: model.py holds the data a project parses
into, core.py what an automaton must be able to do whatever its
behaviour is made of (data, lookups, and the four seams where an
expression is actually evaluated), and payloads.py / introspection.py
the contracts only the platform around it asks for — API serialization,
design-view and metrics introspection.

An automaton compiled to literal Python subclasses CoreAutomaton and
overrides those four seams; which of the mixins below it also composes
is a question of which features that product enables, not of what an
automaton is.

Everything the rest of the codebase already imports from this module —
Action, State, Signal, Reaction, EnvKey, Source, MemoryArchive, every
Payload type, JsSnippet, DeferredExpression, pressable_actions — is
re-exported here unchanged, so no import anywhere had to move."""
from __future__ import annotations

from .core import CoreAutomaton, DeferredExpression, JsSnippet, _TaskEval
from .introspection import IntrospectionMixin
from .model import (
    Action,
    ActionPayload,
    EnvKey,
    EnvKeyPayload,
    MemoryArchive,
    ProjectPayload,
    Reaction,
    ReactionOptionPayload,
    Signal,
    SignalPayload,
    Source,
    SourceDict,
    SourcePayload,
    State,
    StatePayload,
)
from .payloads import PayloadsMixin, pressable_actions

__all__ = [
    "Action", "ActionPayload", "Automaton", "CompiledAutomaton", "CoreAutomaton", "DeferredExpression",
    "EnvKey", "EnvKeyPayload", "IntrospectionMixin", "JsSnippet", "MemoryArchive", "PayloadsMixin",
    "ProjectPayload", "Reaction", "ReactionOptionPayload", "Signal", "SignalPayload", "Source",
    "SourceDict", "SourcePayload", "State", "StatePayload", "pressable_actions",
]


class Automaton(CoreAutomaton, PayloadsMixin, IntrospectionMixin):
    """A project's automaton with every platform contract composed onto
    it — what AutomatonBuilder builds and every service here expects."""


class CompiledAutomaton(CoreAutomaton):
    """Common base every compiled package's own generated automaton class
    subclasses — carries no mixins or compiled behaviour of its own, only what
    isinstance(automaton, CompiledAutomaton) checks against, regardless
    of which package produced the instance."""
