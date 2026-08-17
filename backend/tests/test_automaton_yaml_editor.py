"""AutomatonYamlEditor — every add/edit/delete/reorder operation an "Edit
project" view needs against a project's own index.yml, working directly
on a ruamel.yaml round-trip tree. This class never validates the result
as a whole (that's AutomatonBuilder's own job) — these tests check the
structural edit itself and the payload shape returned, not end-to-end
project validity, except where a test explicitly re-parses the result
through AutomatonBuilder to confirm a round trip stays buildable.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from automaton.automaton_yaml_editor import AutomatonYamlEditor, InitActionTargetError

pytestmark = pytest.mark.contract


BASE_YAML = """\
init-action:
  target: a
signals:
  foo:
    ui-label: Foo signal
    definition: foo definition
  bar:
    ui-label: Bar signal
    definition: bar definition
states:
  a:
    ui-label: State A
    contextual-prompt: hi
    actions:
      - name: go-b
        ui-label: Go to B
        target: b
        trigger: foo >= 50
      - name: go-c
        ui-label: Go to C
        target: c
        trigger: foo >= 50 and bar >= 50
  b:
    ui-label: State B
    contextual-prompt: there
  c:
    ui-label: State C
    contextual-prompt: elsewhere
"""


def _editor(text: str = BASE_YAML) -> AutomatonYamlEditor:
    return AutomatonYamlEditor(text)


def _builds(text: str):
    return AutomatonBuilder().build({"index.yml": text})


class TestToSnakeCase:
    def test_lowercases_and_replaces_non_alnum_with_underscore(self):
        assert AutomatonYamlEditor.to_snake_case("Risk Level!") == "risk_level"

    def test_collapses_repeated_separators(self):
        assert AutomatonYamlEditor.to_snake_case("Risk   Level -- High") == "risk_level_high"

    def test_trims_leading_and_trailing_underscores(self):
        assert AutomatonYamlEditor.to_snake_case("--Risk--") == "risk"


class TestAddState:
    def test_generates_a_numbered_name_and_placeholder_label(self):
        editor = _editor()
        payload = editor.add_state()

        assert payload["key"] == "state-0"
        assert payload["ui_label"] == "New State"
        assert payload["ui_description"] is None
        assert payload["final"] is True
        assert payload["chat"] is True
        assert payload["actions"] == []

    def test_numbering_continues_from_the_highest_existing_state_n(self):
        editor = _editor(BASE_YAML + "  state-0:\n    ui-label: Pre-existing\n    contextual-prompt: x\n")
        payload = editor.add_state()
        assert payload["key"] == "state-1"

    def test_ui_label_collision_gets_a_numeric_suffix(self):
        editor = _editor()
        editor.add_state()  # "New State"
        second = editor.add_state()
        assert second["ui_label"] == "New State 2"

    def test_result_still_builds_and_is_reachable(self):
        editor = _editor()
        payload = editor.add_state()
        text = editor.serialize()

        automaton = _builds(text)
        assert payload["key"] in automaton.states


class TestAddSignal:
    def test_name_is_derived_from_the_generated_ui_label(self):
        editor = _editor()
        payload = editor.add_signal()

        assert payload["ui_label"] == "New Signal"
        assert payload["name"] == "new_signal"
        assert payload["definition"] == ""
        assert payload["attachments"] == {}
        assert payload["error"] is None

    def test_ui_label_and_name_collisions_both_get_suffixed(self):
        editor = _editor()
        editor.add_signal()  # "New Signal" / new_signal
        second = editor.add_signal()
        assert second["ui_label"] == "New Signal 2"
        assert second["name"] == "new_signal_2"

    def test_result_still_builds(self):
        editor = _editor()
        payload = editor.add_signal()
        automaton = _builds(editor.serialize())
        assert any(s.name == payload["name"] for s in automaton.signals)


class TestAddAction:
    def test_generates_a_numbered_name_scoped_to_the_state(self):
        editor = _editor()
        payload = editor.add_action("b")

        assert payload["name"] == "action-0"
        assert payload["ui_label"] == "New Action"
        assert payload["ui_button"] == "New Action"  # falls back through ui-label
        assert payload["target"] == "b"  # self-loop, target omitted
        assert payload["has_trigger"] is False
        assert payload["on-enter"] is None

    def test_numbering_is_scoped_to_the_state_not_the_project(self):
        editor = _editor()
        editor.add_action("a")  # state "a" already has go-b/go-c, no action-N yet -> action-0
        first_b = editor.add_action("b")
        assert first_b["name"] == "action-0"  # state "b" has its own independent counter

    def test_same_ui_label_allowed_in_a_different_state(self):
        editor = _editor()
        editor.add_action("b")  # "New Action"
        other_state = editor.add_action("c")
        assert other_state["ui_label"] == "New Action"  # no suffix — different state's own scope

    def test_ui_label_collision_within_the_same_state_gets_suffixed(self):
        editor = _editor()
        editor.add_action("b")
        second = editor.add_action("b")
        assert second["ui_label"] == "New Action 2"

    def test_result_still_builds(self):
        editor = _editor()
        payload = editor.add_action("b")
        automaton = _builds(editor.serialize())
        assert any(a.name == payload["name"] for a in automaton.states["b"].actions)


class TestSetStateField:
    def test_ui_label_edit_never_touches_the_key(self):
        editor = _editor()
        payload = editor.set_state_field("a", "ui-label", "Renamed A")

        assert payload["key"] == "a"
        assert payload["ui_label"] == "Renamed A"
        automaton = _builds(editor.serialize())
        assert "a" in automaton.states
        assert automaton.states["a"].ui_label == "Renamed A"

    def test_history_cutoff_edit(self):
        editor = _editor()
        payload = editor.set_state_field("a", "history-cutoff", True)
        assert _builds(editor.serialize()).states["a"].history_cutoff is True
        # StatePayload doesn't carry history_cutoff — only confirm no error and a real State result.
        assert payload["key"] == "a"

    def test_contextual_prompt_edit(self):
        editor = _editor()
        editor.set_state_field("a", "contextual-prompt", "brand new prompt")
        assert _builds(editor.serialize()).states["a"].contextual_prompt == "brand new prompt"


class TestSetActionField:
    def test_ui_label_edit_never_touches_the_name(self):
        editor = _editor()
        payload = editor.set_action_field("a", "go-b", "ui-label", "Renamed")

        assert payload["name"] == "go-b"
        assert payload["ui_label"] == "Renamed"

    def test_target_edit(self):
        editor = _editor()
        payload = editor.set_action_field("a", "go-b", "target", "c")
        assert payload["target"] == "c"
        automaton = _builds(editor.serialize())
        action = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        assert action.target == "c"

    def test_action_prompt_edit(self):
        editor = _editor()
        editor.set_action_field("a", "go-b", "action-prompt", "Say hello warmly.")
        automaton = _builds(editor.serialize())
        action = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        assert action.action_prompt == "Say hello warmly."


class TestSetSignalField:
    def test_non_ui_label_field_is_a_plain_edit(self):
        editor = _editor()
        payload = editor.set_signal_field("foo", "definition", "new definition")
        assert payload["name"] == "foo"
        assert payload["definition"] == "new definition"

    def test_ui_label_edit_that_does_not_change_the_derived_name_stays_in_place(self):
        editor = _editor()
        payload = editor.set_signal_field("foo", "ui-label", "FOO")  # snake_case("FOO") == "foo"
        assert payload["name"] == "foo"
        assert payload["ui_label"] == "FOO"

    def test_ui_label_edit_that_changes_the_derived_name_renames_the_signal(self):
        editor = _editor()
        payload = editor.set_signal_field("foo", "ui-label", "Risk Level")
        assert payload["name"] == "risk_level"
        assert payload["ui_label"] == "Risk Level"
        automaton = _builds(editor.serialize())
        assert "risk_level" in {s.name for s in automaton.signals}
        assert "foo" not in {s.name for s in automaton.signals}


class TestRenameSignal:
    def test_renames_the_key_and_returns_the_updated_payload(self):
        editor = _editor()
        payload = editor.rename_signal("foo", "danger")
        assert payload["name"] == "danger"
        automaton = _builds(editor.serialize())
        assert {s.name for s in automaton.signals} == {"danger", "bar"}

    def test_collision_with_an_existing_signal_name_gets_suffixed(self):
        editor = _editor()
        payload = editor.rename_signal("foo", "bar")
        assert payload["name"] == "bar_2"

    def test_preserves_the_other_signals_own_order(self):
        editor = _editor()
        editor.rename_signal("foo", "danger")
        names = list(editor._raw["signals"].keys())
        assert names == ["danger", "bar"]

    def test_rewrites_every_trigger_referencing_the_old_name_via_ast_not_text(self):
        editor = _editor()
        editor.rename_signal("foo", "danger")
        automaton = _builds(editor.serialize())
        go_b = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        go_c = next(a for a in automaton.states["a"].actions if a.name == "go-c")
        assert go_b.trigger == "danger >= 50"
        assert go_c.trigger == "danger >= 50 and bar >= 50"

    def test_a_trigger_not_referencing_the_signal_is_left_untouched(self):
        editor = _editor()
        editor.rename_signal("foo", "danger")
        automaton = _builds(editor.serialize())
        # go-c's own "bar >= 50" clause survives unchanged (bar was never renamed).
        go_c = next(a for a in automaton.states["a"].actions if a.name == "go-c")
        assert "bar >= 50" in go_c.trigger


class TestDeleteState:
    def test_removes_the_state(self):
        editor = _editor()
        editor.delete_state("c")
        assert "c" not in editor._raw["states"]

    def test_removes_incoming_actions_from_other_states(self):
        editor = _editor()
        editor.delete_state("c")
        automaton = _builds(editor.serialize())
        action_names = {a.name for a in automaton.states["a"].actions}
        assert "go-c" not in action_names
        assert "go-b" in action_names

    def test_outgoing_actions_vanish_along_with_the_state_itself(self):
        # State "a" owns go-b/go-c — deleting "a" removes them implicitly,
        # not via any special-cased cascade of its own.
        editor = _editor("""\
init-action:
  target: b
states:
  a:
    ui-label: A
    contextual-prompt: hi
    actions:
      - name: go-b
        target: b
  b:
    ui-label: B
    contextual-prompt: there
""")
        editor.delete_state("a")
        automaton = _builds(editor.serialize())
        assert "a" not in automaton.states

    def test_refuses_to_delete_the_init_actions_own_target(self):
        editor = _editor()
        with pytest.raises(InitActionTargetError):
            editor.delete_state("a")
        # Left untouched.
        assert "a" in editor._raw["states"]


class TestDeleteAction:
    def test_removes_only_that_action_no_cascade(self):
        editor = _editor()
        editor.delete_action("a", "go-b")
        automaton = _builds(editor.serialize())
        action_names = {a.name for a in automaton.states["a"].actions}
        assert action_names == {"go-c"}
        assert "b" in automaton.states  # state "b" itself untouched


class TestDeleteSignal:
    def test_removes_the_signal_itself(self):
        editor = _editor()
        editor.delete_signal("bar")
        assert "bar" not in editor._raw["signals"]

    def test_bool_op_drops_just_the_referencing_operand_when_others_survive(self):
        editor = _editor()
        editor.delete_signal("bar")
        automaton = _builds(editor.serialize())
        go_c = next(a for a in automaton.states["a"].actions if a.name == "go-c")
        # "foo >= 50 and bar >= 50" loses only its own "bar >= 50" operand.
        assert go_c.trigger == "foo >= 50"

    def test_a_lone_non_bool_op_trigger_is_removed_entirely_when_it_references_the_signal(self):
        editor = _editor()
        editor.delete_signal("foo")
        automaton = _builds(editor.serialize())
        go_b = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        assert go_b.trigger is None  # action survives, now manual-only

    def test_a_trigger_not_referencing_the_deleted_signal_is_untouched(self):
        editor = _editor("""\
init-action:
  target: a
signals:
  foo:
    ui-label: Foo
    definition: foo definition
  bar:
    ui-label: Bar
    definition: bar definition
states:
  a:
    ui-label: A
    contextual-prompt: hi
    actions:
      - name: go-b
        target: b
        trigger: bar >= 50
  b:
    ui-label: B
    contextual-prompt: there
""")
        editor.delete_signal("foo")
        automaton = _builds(editor.serialize())
        go_b = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        assert go_b.trigger == "bar >= 50"

    def test_all_operands_referencing_the_signal_empties_the_trigger_field(self):
        editor = _editor("""\
init-action:
  target: a
signals:
  foo:
    ui-label: Foo
    definition: foo definition
states:
  a:
    ui-label: A
    contextual-prompt: hi
    actions:
      - name: go-b
        target: b
        trigger: foo >= 50 or foo <= 0
  b:
    ui-label: B
    contextual-prompt: there
""")
        editor.delete_signal("foo")
        automaton = _builds(editor.serialize())
        go_b = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        assert go_b.trigger is None


class TestReorderActions:
    def test_moves_the_action_to_the_given_position(self):
        editor = _editor()
        payload = editor.reorder_actions("a", "go-c", 0)
        assert [a["name"] for a in payload] == ["go-c", "go-b"]

    def test_result_reflects_in_the_serialized_yaml(self):
        editor = _editor()
        editor.reorder_actions("a", "go-c", 0)
        automaton = _builds(editor.serialize())
        assert [a.name for a in automaton.states["a"].actions] == ["go-c", "go-b"]

    def test_moving_to_its_own_current_position_is_a_noop(self):
        editor = _editor()
        payload = editor.reorder_actions("a", "go-b", 0)
        assert [a["name"] for a in payload] == ["go-b", "go-c"]

    def test_unknown_action_name_raises(self):
        editor = _editor()
        with pytest.raises(ValueError):
            editor.reorder_actions("a", "does-not-exist", 0)

    def test_out_of_range_position_raises(self):
        editor = _editor()
        with pytest.raises(ValueError):
            editor.reorder_actions("a", "go-b", 5)
        with pytest.raises(ValueError):
            editor.reorder_actions("a", "go-b", -1)


class TestSerializeRoundTrip:
    def test_untouched_document_serializes_back_unchanged(self):
        editor = _editor()
        assert editor.serialize() == BASE_YAML

    def test_a_single_field_edit_leaves_everything_else_byte_identical(self):
        editor = _editor()
        editor.set_state_field("b", "ui-label", "Renamed B")
        result = editor.serialize()
        # Every other line survives untouched — only "State B" -> "Renamed B" differs.
        before_lines = BASE_YAML.splitlines()
        after_lines = result.splitlines()
        assert len(before_lines) == len(after_lines)
        diffs = [i for i, (a, b) in enumerate(zip(before_lines, after_lines)) if a != b]
        assert diffs == [before_lines.index("  b:") + 1]
