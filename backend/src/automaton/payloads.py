"""PayloadsMixin — turning an automaton's own data into the shapes the
frontend reads, plus the one filter that decides which actions a user
may press.

A platform contract, not part of being an automaton: a compiled product
that never serves a state payload to a design view has no use for any of
it. Composed onto CoreAutomaton in automaton.py."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model import (
        Action, ActionPayload, EnvKey, EnvKeyPayload, Reaction, ReactionOptionPayload,
        Signal, SignalPayload, Source, SourcePayload, State, StatePayload,
    )


def manual_actions_for(actions: list[ActionPayload], auto_tracking_enabled: bool) -> list[ActionPayload]:
    return [a for a in actions if not a["has_trigger"] or not auto_tracking_enabled]


class PayloadsMixin(object):

    @staticmethod
    def get_action_payload(action: Action) -> ActionPayload:
        """Serializes `action` for the frontend. Deliberately omits
        `trigger`'s raw expression — that's internal transition logic,
        only ever exposed via the "Edit project" view's Inspect panel."""
        return {
            "name": action.name,
            "ui_label": action.ui_label,
            "ui_button": action.ui_button,
            "ui_description": action.ui_description,
            "target": action.target,
            "has_trigger": action.trigger is not None,
            "task": action.task,
            "on-exit": action.on_exit,
        }

    @staticmethod
    def get_signal_payload(signal: Signal) -> SignalPayload:
        """Serializes `signal` for the frontend. `attachments` stays
        empty deliberately: shipping full (base64) file content on every
        call would be wasteful when only the names are usually needed."""
        return {
            "name": signal.name,
            "ui_label": signal.ui_label,
            "ui_description": signal.ui_description,
            "definition": signal.definition,
            "attachments": {},
            "error": None,
        }

    @staticmethod
    def get_env_key_payload(env_key: EnvKey) -> EnvKeyPayload:
        """Serializes `env_key` for the frontend — mirrors
        get_signal_payload's role for Signal."""
        return {
            "name": env_key.name,
            "ui_description": env_key.ui_description,
            "value": env_key.value,
            "ai_definition": env_key.ai_definition,
        }

    @staticmethod
    def get_source_payload(source: Source) -> SourcePayload:
        """Serializes `source` for the frontend — mirrors
        get_env_key_payload's role for EnvKey."""
        return {
            "name": source.name,
            "ui_label": source.ui_label,
            "ui_description": source.ui_description,
            "ai_definition": source.ai_definition,
            "url": source.url,
        }

    @staticmethod
    def get_reaction_option_payload(reaction: Reaction) -> ReactionOptionPayload:
        """Serializes `reaction` for the frontend's reaction picker —
        deliberately omits definition/ui_description, the AI-facing
        fields that decide when the bot itself would use it, same
        reasoning as get_action_payload's own omission of `trigger`."""
        return {"key": reaction.name, "ui_label": reaction.ui_label}

    def get_state_payload(self, state: State) -> StatePayload:
        """Serializes `state` for the frontend. Safety barrier: the
        reserved implicit state ("") must never reach a caller outside
        TurnService.open_if_needed. Not static, unlike its siblings above:
        `reactions` is this automaton's own whole vocabulary, not
        something `state` itself carries."""
        if state.key == "":
            raise RuntimeError("Refusing to serialize the implicit initial state ('').")
        return {
            "key": state.key,
            "ui_label": state.ui_label,
            "ui_description": state.ui_description,
            "final": state.final,
            "chat_enabled": state.chat_enabled,
            "reactions": [self.get_reaction_option_payload(r) for r in self.reactions],
            # XXX Compiled automaton requirement - do not touch.
            # XXX self, not the Automaton class: this now lives in a mixin,
            # XXX which a compiled automaton composes without ever being an
            # XXX Automaton — a hardcoded class name would not resolve.
            "actions": [self.get_action_payload(a) for a in state.actions],
            "ai_may_read_sources": list(state.ai_may_read_sources),
            "ai_must_read_sources": list(state.ai_must_read_sources),
            "ai_may_write_sources": list(state.ai_may_write_sources),
            "input": list(state.input),
            "output": list(state.output),
        }
