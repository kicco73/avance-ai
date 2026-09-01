"""Structural (never textual) editing of a project's index.yml, working
against a ruamel.yaml round-trip tree so comments/formatting survive
edits untouched. Never validates the result — AutomatonBuilder does
that once serialize() feeds the text back through it."""
from __future__ import annotations

import ast
import io
import re
from typing import Any, Mapping

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from automaton.automaton import ActionPayload, EnvKeyPayload, ProjectPayload, SignalPayload, StatePayload
from automaton.trigger_expression_analyzer import TriggerExpressionAnalyzer


class InitActionTargetError(Exception):
    """Raised by delete_state when asked to delete the state init-action
    targets — the automaton's required entry point. Lets a caller render
    more specific guidance than a generic "target is not a valid state"."""


class AutomatonYamlEditor:

    def __init__(self, raw_text: str) -> None:
        self._yaml = YAML(typ='rt')
        # Matches real projects' hand-authored style (a sequence's "- "
        # sits 2 spaces past its parent key) — ruamel's default indent
        # would otherwise reformat every action list on a no-op edit.
        self._yaml.indent(mapping=2, sequence=4, offset=2)
        # Without this, round-trip mode re-quotes scalars in its own
        # preferred style, so an untouched value could gain/lose quotes.
        self._yaml.preserve_quotes = True
        # Ruamel's default 80-column width would silently line-wrap any
        # long scalar it re-dumps, even one an edit never touched.
        self._yaml.width = 1_000_000
        self._raw = self._yaml.load(raw_text)

    def serialize(self) -> str:
        stream = io.StringIO()
        self._yaml.dump(self._raw, stream)
        return stream.getvalue()

    @staticmethod
    def to_snake_case(text: str) -> str:
        lowered = text.lower()
        collapsed = re.sub(r'[^a-z0-9]+', '_', lowered)
        return collapsed.strip('_')

    # ------------------------------------------------------------------
    # Internal: raw-tree accessors, name/label generation, payload builders.
    # ------------------------------------------------------------------

    def _states(self) -> CommentedMap:
        return self._raw.setdefault("states", CommentedMap())

    def _signals(self) -> CommentedMap:
        return self._raw.setdefault("signals", CommentedMap())

    def _env(self) -> CommentedMap:
        return self._raw.setdefault("env", CommentedMap())

    def _state(self, state_name: str) -> CommentedMap:
        try:
            return self._states()[state_name]
        except KeyError:
            raise ValueError(f"State '{state_name}' not found.") from None

    def _signal(self, signal_name: str) -> CommentedMap:
        try:
            return self._signals()[signal_name]
        except KeyError:
            raise ValueError(f"Signal '{signal_name}' not found.") from None

    def _env_key(self, name: str) -> CommentedMap:
        env = self._env()
        try:
            raw = env[name]
        except KeyError:
            raise ValueError(f"Env key '{name}' not found.") from None
        # A bare `key:` declaration (no nested fields at all) parses as
        # None, not {} — normalized in place the first time it's touched
        # so every other accessor below can treat it as a plain mapping.
        if raw is None:
            raw = env[name] = CommentedMap()
        return raw

    def _actions(self, state_name: str) -> CommentedSeq:
        return self._state(state_name).setdefault("actions", CommentedSeq())

    def _find_action(self, state_name: str, action_name: str) -> CommentedMap:
        for raw_action in self._actions(state_name):
            if raw_action.get("name") == action_name:
                return raw_action
        raise ValueError(f"Action '{action_name}' not found in state '{state_name}'.")

    @staticmethod
    def _next_numbered_name(prefix: str, existing_names: set) -> str:
        """The first `{prefix}-N` past whatever's in use — one past the
        *highest* N taken, never backfilling a gap from a deleted entry."""
        pattern = re.compile(rf'^{re.escape(prefix)}-(\d+)$')
        highest = -1
        for name in existing_names:
            match = pattern.match(name)
            if match:
                highest = max(highest, int(match.group(1)))
        return f"{prefix}-{highest + 1}"

    @staticmethod
    def _unique_ui_label(base: str, existing_labels: set) -> str:
        if base not in existing_labels:
            return base
        suffix = 2
        while f"{base} {suffix}" in existing_labels:
            suffix += 1
        return f"{base} {suffix}"

    @staticmethod
    def _unique_signal_name(base: str, existing_names: set) -> str:
        base = base or "signal"
        if base not in existing_names:
            return base
        suffix = 2
        while f"{base}_{suffix}" in existing_names:
            suffix += 1
        return f"{base}_{suffix}"

    def _existing_state_ui_labels(self) -> set:
        return {raw_state.get("ui-label", key) for key, raw_state in self._states().items()}

    def _existing_signal_ui_labels(self) -> set:
        return {raw_signal.get("ui-label", name) for name, raw_signal in self._signals().items()}

    def _existing_action_ui_labels(self, state_name: str) -> set:
        return {raw_action.get("ui-label") or raw_action["name"] for raw_action in self._actions(state_name)}

    def _state_payload(self, state_name: str) -> StatePayload:
        raw_state = self._state(state_name)
        raw_actions = raw_state.get("actions") or []
        return {
            "key": state_name,
            "ui_label": raw_state.get("ui-label", state_name),
            "ui_description": raw_state["ui-description"].strip() if raw_state.get("ui-description") else None,
            "final": len(raw_actions) == 0,
            "chat": raw_state.get("chat", True),
            "actions": [self._action_payload_from_raw(raw_action, state_name) for raw_action in raw_actions],
        }

    @staticmethod
    def _action_payload_from_raw(raw_action: Mapping[str, Any], state_name: str) -> ActionPayload:
        return {
            "name": raw_action["name"],
            "ui_label": raw_action.get("ui-label") or raw_action["name"],
            "ui_button": raw_action.get("ui-button") or raw_action.get("ui-label") or raw_action["name"],
            "ui_description": raw_action["ui-description"].strip() if raw_action.get("ui-description") else None,
            "target": raw_action.get("target", state_name),
            "has_trigger": raw_action.get("trigger") is not None,
            "has_actuator": raw_action.get("actuator") is not None,
            "on-enter": raw_action.get("on-enter"),
        }

    def _action_payload(self, state_name: str, action_name: str) -> ActionPayload:
        return self._action_payload_from_raw(self._find_action(state_name, action_name), state_name)

    def _signal_payload(self, signal_name: str) -> SignalPayload:
        raw_signal = self._signal(signal_name)
        definition = (raw_signal.get("definition") or "").strip()
        return {
            "name": signal_name,
            "ui_label": raw_signal.get("ui-label", signal_name),
            "ui_description": raw_signal["ui-description"].strip() if raw_signal.get("ui-description") else (definition or None),
            "definition": definition,
            # This class never resolves attachment filenames into real
            # MemoryArchive objects — that needs the project's file
            # store, not available here — so always empty.
            "attachments": {},
            "error": None,
        }

    def _project_payload(self) -> ProjectPayload:
        raw_project = self._raw.get("project") or {}
        return {
            "id": raw_project.get("id"),
            "ui_label": raw_project.get("ui-label"),
            "ui_description": raw_project.get("ui-description"),
            "talk_enabled": raw_project.get("talk-enabled", True),
            "signal_tracking_on_ai_message": raw_project.get("signal-tracking-on-ai-message", False),
            "general_prompt": self._raw.get("general-prompt", ""),
        }

    def _env_key_payload(self, name: str) -> EnvKeyPayload:
        raw_env_key = self._env_key(name)
        ui_description = raw_env_key.get("ui-description")
        return {
            "name": name,
            "ui_description": ui_description.strip() if ui_description else None,
            "value": raw_env_key.get("value") or "",
        }

    # ------------------------------------------------------------------
    # Add
    # ------------------------------------------------------------------

    def add_state(self) -> StatePayload:
        states = self._states()
        name = self._next_numbered_name("state", set(states.keys()))
        ui_label = self._unique_ui_label("New State", self._existing_state_ui_labels())
        states[name] = CommentedMap({
            "ui-label": ui_label,
            "contextual-prompt": "",
        })
        return self._state_payload(name)

    def add_signal(self) -> SignalPayload:
        signals = self._signals()
        ui_label = self._unique_ui_label("New Signal", self._existing_signal_ui_labels())
        name = self._unique_signal_name(self.to_snake_case(ui_label), set(signals.keys()))
        signals[name] = CommentedMap({
            "ui-label": ui_label,
            "definition": "",
        })
        return self._signal_payload(name)

    def add_env_key(self) -> EnvKeyPayload:
        """Unlike a signal, an env key has no separate ui-label — the key
        itself is the only name — so it's suffixed via _unique_signal_name
        (valid identifier chars), never _next_numbered_name's "-N" suffix."""
        env = self._env()
        name = self._unique_signal_name("new_env_key", set(env.keys()))
        env[name] = CommentedMap({"value": ""})
        return self._env_key_payload(name)

    def add_action(self, state_name: str) -> ActionPayload:
        actions = self._actions(state_name)
        existing_names = {raw_action.get("name") for raw_action in actions}
        name = self._next_numbered_name("action", existing_names)
        ui_label = self._unique_ui_label("New Action", self._existing_action_ui_labels(state_name))
        actions.append(CommentedMap({
            "name": name,
            "ui-label": ui_label,
        }))
        return self._action_payload(state_name, name)

    # ------------------------------------------------------------------
    # Edit
    # ------------------------------------------------------------------

    def set_state_field(self, state_name: str, field: str, value) -> StatePayload:
        self._state(state_name)[field] = value
        return self._state_payload(state_name)

    def set_action_field(self, state_name: str, action_name: str, field: str, value) -> ActionPayload:
        raw_action = self._find_action(state_name, action_name)
        # An empty trigger means manual-only, so the key is removed
        # rather than left holding "" — has_trigger would otherwise
        # report True for an action the user just cleared. An emptied
        # 'env' mapping is removed the same way, rather than lingering as
        # `env: {}` (behaviorally identical either way — AutomatonBuilder
        # treats a falsy env the same as a missing one — but this keeps
        # the YAML clean).
        if field in ("trigger", "env", "actuator") and not value:
            raw_action.pop(field, None)
        else:
            raw_action[field] = value
        return self._action_payload(state_name, action_name)

    def _init_action_payload(self) -> ActionPayload:
        """The init-action is an action like any other — same payload,
        built the same way as _action_payload_from_raw — with two
        structural exceptions to account for: its raw YAML dict has no
        'name' key of its own (injected here as the constant
        "init-action"), and it has no containing state to self-loop
        back to were 'target' ever missing (there's no real "initial
        state" it belongs to — "" stands in, the same reserved
        pseudo-state key used elsewhere). 'trigger' is forced off
        regardless of any stray key sitting in the YAML: unlike every
        other field here, AutomatonBuilder's own _build_init_action
        never reads it, so honoring it would describe an action that
        doesn't actually exist once built."""
        init_action = self._raw.get("init-action") or {}
        payload = self._action_payload_from_raw({**init_action, "name": "init-action"}, "")
        payload["has_trigger"] = False
        return payload

    def set_signal_field(self, signal_name: str, field: str, value) -> SignalPayload:
        if field == "ui-label":
            self._signal(signal_name)["ui-label"] = value
            derived_name = self.to_snake_case(value)
            if derived_name != signal_name:
                return self.rename_signal(signal_name, derived_name)
            return self._signal_payload(signal_name)
        self._signal(signal_name)[field] = value
        return self._signal_payload(signal_name)

    def set_env_key_field(self, name: str, field: str, value) -> EnvKeyPayload:
        """Same "editing this field renames the entry" convention as
        set_signal_field's 'ui-label' case, but 'name' itself is the
        field that does it — an env key has no separate ui-label."""
        if field == "name":
            derived_name = self.to_snake_case(value)
            if derived_name and derived_name != name:
                return self.rename_env_key(name, derived_name)
            return self._env_key_payload(name)
        self._env_key(name)[field] = value
        return self._env_key_payload(name)

    def set_project_field(self, field: str, value) -> ProjectPayload:
        """The optional top-level `project:` mapping — id/ui-label/
        ui-description/talk-enabled/signal-tracking-on-ai-message — plus
        'general-prompt', which despite belonging to this same edit form
        is actually its own top-level YAML key, not nested under
        `project:` at all (see AutomatonBuilder.build's own
        general_prompt=raw.get("general-prompt", "")). A falsy `id`
        removes the key rather than writing an empty string, which
        AutomatonBuilder would reject as an invalid identifier."""
        if field == "general-prompt":
            if value:
                self._raw["general-prompt"] = value
            else:
                self._raw.pop("general-prompt", None)
            return self._project_payload()
        project = self._raw.setdefault("project", CommentedMap())
        if field == "id" and not value:
            project.pop("id", None)
        else:
            project[field] = value
        return self._project_payload()

    def set_init_action_field(self, field: str, value) -> StatePayload | ActionPayload:
        """Every editable field of the init-action itself. 'target' is
        handled by set_init_action_target below; the init-action lives
        outside `states:` entirely, so the regular action lookup can't
        reach it. 'env' gets the same "empty removes the key" treatment
        as a regular action's own env field (see set_action_field) —
        AutomatonBuilder treats a falsy env the same as a missing one,
        so this just keeps the YAML clean."""
        if field == "target":
            return self.set_init_action_target(value)
        init_action = self._raw.setdefault("init-action", CommentedMap())
        if field == "env" and not value:
            init_action.pop(field, None)
        else:
            init_action[field] = value
        return self._init_action_payload()

    def set_init_action_target(self, state_name: str) -> StatePayload:
        """Moves the automaton's start state. There's no way to unset it
        from here, only to move it to a different state (see
        delete_state's InitActionTargetError for the "can't delete the current one" guard)."""
        self._state(state_name)  # raises ValueError if unknown, same as every other set_*_field
        init_action = self._raw.setdefault("init-action", CommentedMap())
        init_action["target"] = state_name
        return self._state_payload(state_name)

    @staticmethod
    def _rename_key_preserving_comments(mapping: CommentedMap, old_key: str, new_key: str) -> None:
        """A structural key swap, not a textual replace — rebuilds
        `mapping` in place so every other entry's attached comment stays
        put; the renamed entry's own comment is carried over explicitly."""
        items = list(mapping.items())
        original_comments = dict(mapping.ca.items)
        mapping.clear()
        mapping.ca.items.clear()
        for key, value in items:
            actual_key = new_key if key == old_key else key
            mapping[actual_key] = value
            if key in original_comments:
                mapping.ca.items[actual_key] = original_comments[key]

    def rename_signal(self, old_name: str, new_name: str) -> SignalPayload:
        signals = self._signals()
        if old_name not in signals:
            raise ValueError(f"Signal '{old_name}' not found.")
        existing_names = set(signals.keys()) - {old_name}
        unique_new_name = self._unique_signal_name(new_name, existing_names)

        self._rename_key_preserving_comments(signals, old_name, unique_new_name)
        self._rename_namespaced_ref_in_triggers("signal", old_name, unique_new_name)

        return self._signal_payload(unique_new_name)

    def rename_env_key(self, old_name: str, new_name: str) -> EnvKeyPayload:
        env = self._env()
        if old_name not in env:
            raise ValueError(f"Env key '{old_name}' not found.")
        existing_names = set(env.keys()) - {old_name}
        unique_new_name = self._unique_signal_name(new_name, existing_names)

        self._rename_key_preserving_comments(env, old_name, unique_new_name)
        self._rename_namespaced_ref_in_triggers("env", old_name, unique_new_name)

        return self._env_key_payload(unique_new_name)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete_state(self, state_name: str) -> None:
        init_action = self._raw.get("init-action") or {}
        if init_action.get("target") == state_name:
            raise InitActionTargetError(
                f"'{state_name}' is the init-action's own target and can't be deleted — "
                "point init-action at a different state first."
            )

        states = self._states()
        if state_name in states:
            del states[state_name]

        for containing_key, raw_state in states.items():
            raw_actions = raw_state.get("actions") or []
            # A self-loop (target omitted) targets the action's own
            # containing state, never the one just deleted — resolved
            # per action, not assumed, before comparing.
            remaining = [a for a in raw_actions if a.get("target", containing_key) != state_name]
            if len(remaining) != len(raw_actions):
                raw_state["actions"] = remaining

    def delete_action(self, state_name: str, action_name: str) -> None:
        actions = self._actions(state_name)
        self._state(state_name)["actions"] = [a for a in actions if a.get("name") != action_name]

    def delete_signal(self, signal_name: str) -> None:
        signals = self._signals()
        if signal_name in signals:
            del signals[signal_name]

        self._transform_triggers_referencing(
            "signal", signal_name, lambda tree: self._strip_namespaced_ref_from_trigger(tree, "signal", signal_name)
        )

    def delete_env_key(self, name: str) -> None:
        env = self._env()
        if name in env:
            del env[name]

        self._transform_triggers_referencing(
            "env", name, lambda tree: self._strip_namespaced_ref_from_trigger(tree, "env", name)
        )

    # ------------------------------------------------------------------
    # Reorder
    # ------------------------------------------------------------------

    def reorder_actions(self, state_name: str, action_name: str, position: int) -> list:
        actions = self._actions(state_name)
        current_index = next((i for i, a in enumerate(actions) if a.get("name") == action_name), None)
        if current_index is None:
            raise ValueError(f"Action '{action_name}' not found in state '{state_name}'.")
        if not (0 <= position < len(actions)):
            raise ValueError(
                f"Position {position} is out of range for state '{state_name}''s {len(actions)} action(s)."
            )

        # Moves the existing node itself (pop + insert), never rebuilds
        # it, so any comment/formatting already attached to that one
        # action's own entry travels with it.
        node = actions.pop(current_index)
        actions.insert(position, node)

        return [self._action_payload_from_raw(a, state_name) for a in actions]

    # ------------------------------------------------------------------
    # Shared trigger-tree traversal for rename/delete of signals and env keys.
    # ------------------------------------------------------------------

    def _transform_triggers_referencing(self, namespace: str, name: str, transform) -> None:
        """Walks every action of every state, rewriting any `trigger`
        that references `<namespace>.<name>` via `transform`. An
        unrelated trigger is untouched; returning None removes the trigger field."""
        for containing_key, raw_state in self._states().items():
            for raw_action in raw_state.get("actions") or []:
                trigger = raw_action.get("trigger")
                if not trigger:
                    continue
                tree = ast.parse(trigger, mode="eval").body
                if name not in TriggerExpressionAnalyzer.namespace_attrs(tree, namespace):
                    continue
                new_node = transform(tree)
                if new_node is None:
                    del raw_action["trigger"]
                else:
                    raw_action["trigger"] = ast.unparse(new_node)

    def _rename_namespaced_ref_in_triggers(self, namespace: str, old_name: str, new_name: str) -> None:
        def transform(tree: ast.AST) -> ast.AST:
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == namespace and node.attr == old_name
                ):
                    node.attr = new_name
            return tree
        self._transform_triggers_referencing(namespace, old_name, transform)

    def _strip_namespaced_ref_from_trigger(self, node: ast.AST, namespace: str, name: str) -> ast.AST | None:
        """A BoolOp (and/or) drops just the operand(s) referencing
        `<namespace>.<name>`. Any other node is all-or-nothing: emptied
        if the reference appears anywhere inside it, untouched otherwise."""
        if isinstance(node, ast.BoolOp):
            kept = [
                child for child in (
                    self._strip_namespaced_ref_from_trigger(operand, namespace, name) for operand in node.values
                )
                if child is not None
            ]
            if not kept:
                return None
            if len(kept) == 1:
                return kept[0]
            node.values = kept
            return node
        references_name = any(
            isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id == namespace and n.attr == name
            for n in ast.walk(node)
        )
        return None if references_name else node
