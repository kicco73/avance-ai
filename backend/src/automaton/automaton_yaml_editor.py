"""Structural (never textual) editing of a project's own index.yml — every
add/edit/delete/reorder operation an "Edit project" view needs, all
working directly against a ruamel.yaml round-trip tree (so comments and
formatting survive edits untouched wherever the edit itself doesn't touch
them) instead of AutomatonBuilder's own read-only parse. Every payload
this hands back mirrors AutomatonBuilder's own field-resolution rules
(ui-label fallbacks, ui-button's own fallback chain, has_trigger, final
derived from actions, ...) exactly, so a caller sees the same object
AutomatonBuilder would eventually build from the same YAML — but this
class itself never validates the result as a whole (unknown trigger
names, mutually exclusive fields, ...): that's AutomatonBuilder's own
job, run by whatever calls serialize() and hands the text to
ProjectService.put_project_file().

Two narrow, purely cosmetic round-trip gaps in ruamel.yaml itself, never
worth fixing with a custom representer given neither changes anything a
build ever sees: a blank line's own trailing whitespace inside a literal
block scalar (`|`) is dropped on re-dump, and a boolean's original
spelling (`True`/`TRUE` vs `true`) isn't retained — re-dumped in
whatever casing ruamel's own representer prefers. Both semantically
inert (YAML treats every spelling identically either way) and only ever
surface on a field an edit didn't itself touch.
"""
from __future__ import annotations

import ast
import io
import re

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from automaton.automaton import ActionPayload, SignalPayload, StatePayload, trigger_signal_names


class InitActionTargetError(Exception):
    """Raised by delete_state when asked to delete the one state
    init-action targets — the automaton's own required entry point, so
    deleting it would leave nothing to ever start a session at. A plain
    ValueError from re-validating the result (see AutomatonBuilder.build)
    would eventually catch this too, but only as a generic "target 'x' is
    not a valid state" — this dedicated type lets a caller (see
    ChatService/the REST layer) render its own, more specific guidance
    instead ("this state can't be deleted while init-action still points
    at it")."""


class AutomatonYamlEditor:

    def __init__(self, raw_text: str) -> None:
        self._yaml = YAML(typ='rt')
        # Matches every real project's own hand-authored style (see
        # samples/*/index.yml — a sequence's own "- " sits 2 spaces past
        # its parent key, not flush with it), which ruamel's own default
        # indent doesn't reproduce on dump — left at the default, a
        # no-op edit would still reformat every action list in the file.
        self._yaml.indent(mapping=2, sequence=4, offset=2)
        # Round-trip mode re-quotes every scalar in its own preferred
        # style by default (dropping "Mood" down to Mood, say) unless
        # told to keep whatever quoting the original author actually
        # wrote — an untouched value should never gain or lose quotes
        # just from loading and re-dumping it.
        self._yaml.preserve_quotes = True
        # Ruamel's own default 80-column width silently line-wraps any
        # long scalar it re-dumps, even one an edit never touched — a
        # quoted ui-description that was one line in the original file
        # would otherwise come back split across two.
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
    # Internal: raw-tree accessors, name/label generation, payload
    # builders — every one of these mirrors AutomatonBuilder's own
    # resolution rules (see that module's _build_state/_build_action/
    # _build_signal/get_state_payload) so a payload this class returns is
    # never a different shape from the one AutomatonBuilder would
    # eventually produce from the same YAML.
    # ------------------------------------------------------------------

    def _states(self) -> CommentedMap:
        return self._raw.setdefault("states", CommentedMap())

    def _signals(self) -> CommentedMap:
        return self._raw.setdefault("signals", CommentedMap())

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

    def _actions(self, state_name: str) -> CommentedSeq:
        return self._state(state_name).setdefault("actions", CommentedSeq())

    def _find_action(self, state_name: str, action_name: str) -> CommentedMap:
        for raw_action in self._actions(state_name):
            if raw_action.get("name") == action_name:
                return raw_action
        raise ValueError(f"Action '{action_name}' not found in state '{state_name}'.")

    @staticmethod
    def _next_numbered_name(prefix: str, existing_names: set) -> str:
        """The first `{prefix}-N` past whatever's already in use — always
        one past the *highest* N already taken, never backfilling a gap
        left by a deleted entry, so a name is never reused within the
        same project."""
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
    def _action_payload_from_raw(raw_action: CommentedMap, state_name: str) -> ActionPayload:
        return {
            "name": raw_action["name"],
            "ui_label": raw_action.get("ui-label") or raw_action["name"],
            "ui_button": raw_action.get("ui-button") or raw_action.get("ui-label") or raw_action["name"],
            "target": raw_action.get("target", state_name),
            "has_trigger": raw_action.get("trigger") is not None,
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
            # MemoryArchive objects (that needs the project's own file
            # store — see AutomatonBuilder.build's own all_archives,
            # built from the uploaded zip's contents, not available
            # here) — always empty, same as add_signal's own payload.
            "attachments": {},
            "error": None,
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
        self._find_action(state_name, action_name)[field] = value
        return self._action_payload(state_name, action_name)

    def set_signal_field(self, signal_name: str, field: str, value) -> SignalPayload:
        if field == "ui-label":
            self._signal(signal_name)["ui-label"] = value
            derived_name = self.to_snake_case(value)
            if derived_name != signal_name:
                return self.rename_signal(signal_name, derived_name)
            return self._signal_payload(signal_name)
        self._signal(signal_name)[field] = value
        return self._signal_payload(signal_name)

    def set_init_action_target(self, state_name: str) -> StatePayload:
        """Moves the automaton's own start state — the Inspector's own
        editable state card shows this as an always-on "Start" badge (see
        InspectorDetailCard.vue), clickable to make *this* state the new
        one; there's no way to unset it from here, only to move it to a
        different state (see delete_state's own InitActionTargetError for
        the corresponding "can't delete the current one" guard)."""
        self._state(state_name)  # raises ValueError if unknown, same as every other set_*_field
        init_action = self._raw.setdefault("init-action", CommentedMap())
        init_action["target"] = state_name
        return self._state_payload(state_name)

    def rename_signal(self, old_name: str, new_name: str) -> SignalPayload:
        signals = self._signals()
        if old_name not in signals:
            raise ValueError(f"Signal '{old_name}' not found.")
        existing_names = set(signals.keys()) - {old_name}
        unique_new_name = self._unique_signal_name(new_name, existing_names)

        # A structural key swap, not a textual replace — rebuilds the
        # same CommentedMap in place (same object identity, same key
        # order) so every *other* signal's own attached comment stays
        # exactly where it was; the renamed entry's own comment (keyed by
        # name in the parent's .ca, not on the value node itself — a
        # bare pop/reinsert under a new key would otherwise silently
        # drop it) is carried over to the new key explicitly.
        items = list(signals.items())
        original_comments = dict(signals.ca.items)
        signals.clear()
        signals.ca.items.clear()
        for key, value in items:
            actual_key = unique_new_name if key == old_name else key
            signals[actual_key] = value
            if key in original_comments:
                signals.ca.items[actual_key] = original_comments[key]

        self._rename_signal_in_triggers(old_name, unique_new_name)

        return self._signal_payload(unique_new_name)

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

        self._transform_triggers_referencing(signal_name, lambda tree: self._strip_signal_from_trigger(tree, signal_name))

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
    # Shared trigger-tree traversal for rename_signal/delete_signal — see
    # each one's own docstring for the transform it passes in.
    # ------------------------------------------------------------------

    def _transform_triggers_referencing(self, signal_name: str, transform) -> None:
        """Walks every action of every state, ast-parses any `trigger`
        that references `signal_name` (via trigger_signal_names — an
        ast.walk over every `signal.<name>` attribute access at any
        depth, so a signal name that's merely a substring of another
        name or expression token is never mistaken for a real
        reference) and rewrites it through
        `transform(tree) -> ast.AST | None`. A trigger that doesn't
        reference `signal_name` at all is left completely untouched, not
        even re-unparsed, so an edit here never reformats an unrelated
        trigger's own source. `transform` returning None means the
        trigger is now empty — the field itself is removed (the action
        survives, manual-only) rather than regenerated as unparseable
        empty source."""
        for containing_key, raw_state in self._states().items():
            for raw_action in raw_state.get("actions") or []:
                trigger = raw_action.get("trigger")
                if not trigger or signal_name not in trigger_signal_names(trigger):
                    continue
                tree = ast.parse(trigger, mode="eval").body
                new_node = transform(tree)
                if new_node is None:
                    del raw_action["trigger"]
                else:
                    raw_action["trigger"] = ast.unparse(new_node)

    def _rename_signal_in_triggers(self, old_name: str, new_name: str) -> None:
        def transform(tree: ast.AST) -> ast.AST:
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == "signal" and node.attr == old_name
                ):
                    node.attr = new_name
            return tree
        self._transform_triggers_referencing(old_name, transform)

    def _strip_signal_from_trigger(self, node: ast.AST, signal_name: str) -> ast.AST | None:
        """A BoolOp (and/or) drops just the operand(s) that reference
        `signal_name`, collapsing to the sole survivor if exactly one is
        left, or to nothing if none are. Any other node (a comparison, a
        parenthesized sub-expression, ...) is all-or-nothing: emptied
        outright if `signal_name` appears anywhere inside it at all,
        otherwise left completely untouched — there's no finer-grained
        clause to peel out of e.g. a single comparison."""
        if isinstance(node, ast.BoolOp):
            kept = [
                child for child in (
                    self._strip_signal_from_trigger(operand, signal_name) for operand in node.values
                )
                if child is not None
            ]
            if not kept:
                return None
            if len(kept) == 1:
                return kept[0]
            node.values = kept
            return node
        references_signal = any(
            isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id == "signal" and n.attr == signal_name
            for n in ast.walk(node)
        )
        return None if references_signal else node
