"""AutomatonYamlEditor — state/signal/action/init-action edits against a
project's index.yml, working directly on a ruamel.yaml round-trip tree.
These tests check the structural edit itself, not end-to-end validity.
"""
from __future__ import annotations

import pytest

from automaton.automaton_yaml_editor import AutomatonYamlEditor, InitActionTargetError
from automaton_yaml_editor_helpers import BASE_YAML, SOURCE_ARCHIVES, SOURCE_BASE_YAML, builds, make_editor

pytestmark = pytest.mark.contract


def _action(automaton, state: str, name: str):
    return next(a for a in automaton.states[state].actions if a.name == name)


class TestToSnakeCase:
    def test_lowercases_replaces_non_alnum_collapses_separators_and_trims_underscores(self):
        assert AutomatonYamlEditor.to_snake_case("Risk Level!") == "risk_level"
        assert AutomatonYamlEditor.to_snake_case("Risk   Level -- High") == "risk_level_high"
        assert AutomatonYamlEditor.to_snake_case("--Risk--") == "risk"


class TestAddState:
    def test_generates_a_numbered_name_and_placeholder_label_suffixing_collisions_and_still_builds(self):
        editor = make_editor()
        payload = editor.add_state()
        assert payload["key"] == "state-0"
        assert payload["ui_label"] == "New State"
        assert payload["ui_description"] is None
        assert payload["final"] is True
        assert payload["chat"] is True
        assert payload["actions"] == []

        second = editor.add_state()
        assert second["ui_label"] == "New State 2"
        assert payload["key"] in builds(editor.serialize()).states

        continuing = make_editor(BASE_YAML + "  state-0:\n    ui-label: Pre-existing\n    contextual-prompt: x\n")
        assert continuing.add_state()["key"] == "state-1"


class TestAddSignal:
    def test_name_is_derived_from_the_generated_ui_label_collisions_are_suffixed_and_it_still_builds(self):
        editor = make_editor()
        payload = editor.add_signal()
        assert payload["ui_label"] == "New Signal"
        assert payload["name"] == "new_signal"
        assert payload["definition"] == ""
        assert payload["attachments"] == {}
        assert payload["error"] is None

        second = editor.add_signal()
        assert second["ui_label"] == "New Signal 2"
        assert second["name"] == "new_signal_2"

        automaton = builds(editor.serialize())
        assert any(s.name == payload["name"] for s in automaton.signals)


class TestAddAction:
    def test_generates_a_self_loop_with_a_numbered_name_and_still_builds(self):
        editor = make_editor()
        payload = editor.add_action("b")
        assert payload["name"] == "action-0"
        assert payload["ui_label"] == "New Action"
        assert payload["ui_button"] == "New Action"
        assert payload["ui_description"] is None
        assert payload["target"] == "b"
        assert payload["has_trigger"] is False
        assert payload["on-enter"] is None

        automaton = builds(editor.serialize())
        assert any(a.name == payload["name"] for a in automaton.states["b"].actions)

    def test_numbering_and_ui_label_uniqueness_are_scoped_to_the_state_not_the_project(self):
        editor = make_editor()
        editor.add_action("a")
        first_b = editor.add_action("b")
        assert first_b["name"] == "action-0"

        assert editor.add_action("c")["ui_label"] == "New Action"
        assert editor.add_action("b")["ui_label"] == "New Action 2"


class TestSetStateField:
    def test_plain_field_edits_never_touch_the_key(self):
        editor = make_editor()
        payload = editor.set_state_field("a", "ui-label", "Renamed A")
        assert payload["key"] == "a"
        assert payload["ui_label"] == "Renamed A"

        assert editor.set_state_field("a", "history-cutoff", True)["key"] == "a"
        editor.set_state_field("a", "contextual-prompt", "brand new prompt")

        state = builds(editor.serialize()).states["a"]
        assert state.ui_label == "Renamed A"
        assert state.history_cutoff is True
        assert state.contextual_prompt == "brand new prompt"

    def test_ai_source_access_edits_round_trip_through_the_payload_and_the_build(self):
        editor = make_editor(SOURCE_BASE_YAML)
        editor.set_source_field("pino", "ai-definition", "One row per flight.")

        payload = editor.set_state_field("a", "ai-may-read-sources", ["pino"])
        assert payload["ai_may_read_sources"] == ["pino"]
        assert payload["ai_must_read_sources"] == []
        assert payload["ai_may_write_sources"] == []
        state = builds(editor.serialize(), SOURCE_ARCHIVES).states["a"]
        assert state.ai_may_read_sources == ("pino",)
        assert state.ai_must_read_sources == ()

        editor.set_state_field("a", "ai-may-read-sources", [])
        payload = editor.set_state_field("a", "ai-must-read-sources", ["pino"])
        assert payload["ai_must_read_sources"] == ["pino"]
        assert payload["ai_may_read_sources"] == []
        assert builds(editor.serialize(), SOURCE_ARCHIVES).states["a"].ai_must_read_sources == ("pino",)

        payload = editor.set_state_field("a", "ai-may-write-sources", ["pino"])
        assert payload["ai_may_write_sources"] == ["pino"]
        assert "ai-may-write-sources: [pino]" in editor.serialize() or "- pino" in editor.serialize()


class TestSetActionField:
    def test_plain_field_edits_never_touch_the_name(self):
        editor = make_editor()
        payload = editor.set_action_field("a", "go-b", "ui-label", "Renamed")
        assert payload["name"] == "go-b"
        assert payload["ui_label"] == "Renamed"

        assert editor.set_action_field("a", "go-b", "target", "c")["target"] == "c"
        assert editor.set_action_field("a", "go-b", "ui-description", "Goes to B.")["ui_description"] == "Goes to B."
        editor.set_action_field("a", "go-b", "on-enter", "actuator.celebrate()")

        action = _action(builds(editor.serialize()), "a", "go-b")
        assert action.target == "c"
        assert action.ui_description == "Goes to B."
        assert action.on_enter == "actuator.celebrate()"

    def test_trigger_edit_and_clearing_removes_the_key_instead_of_storing_an_empty_string(self):
        editor = make_editor()
        assert editor.set_action_field("a", "go-c", "trigger", "signal.foo < 10")["has_trigger"] is True
        assert _action(builds(editor.serialize()), "a", "go-c").trigger == "signal.foo < 10"

        assert editor.set_action_field("a", "go-b", "trigger", "")["has_trigger"] is False
        assert _action(builds(editor.serialize()), "a", "go-b").trigger is None

    def test_env_edit_and_clearing_removes_the_key_instead_of_storing_an_empty_mapping(self):
        editor = make_editor(BASE_YAML + "env:\n  counter: {}\n")
        editor.set_action_field("a", "go-b", "env", {"counter": "1"})
        assert _action(builds(editor.serialize()), "a", "go-b").env == {"counter": "1"}

        editor.set_action_field("a", "go-b", "env", {})
        assert _action(builds(editor.serialize()), "a", "go-b").env is None


class TestSetInitActionField:
    """The init-action is an action like any other — same payload shape
    as TestSetActionField above, built off the same _action_payload_from_raw."""

    def test_edits_share_the_action_payload_shape_and_has_trigger_is_always_false(self):
        editor = make_editor()
        payload = editor.set_init_action_field("ui-description", "Where every session begins.")
        assert payload["name"] == "init-action"
        assert payload["ui_description"] == "Where every session begins."
        assert builds(editor.serialize()).init_action.ui_description == "Where every session begins."

        assert editor.set_init_action_field("ui-label", "Begin")["ui_button"] == "Begin"

        stray_trigger_yaml = BASE_YAML.replace("init-action:\n  target: a\n", "init-action:\n  target: a\n  trigger: 'True'\n")
        assert make_editor(stray_trigger_yaml).set_init_action_field("ui-label", "Begin")["has_trigger"] is False


class TestSetSignalField:
    def test_plain_edits_stay_in_place_and_a_ui_label_changing_the_derived_name_renames_the_signal(self):
        editor = make_editor()
        payload = editor.set_signal_field("foo", "definition", "new definition")
        assert payload["name"] == "foo"
        assert payload["definition"] == "new definition"

        payload = editor.set_signal_field("foo", "ui-label", "FOO")
        assert payload["name"] == "foo"
        assert payload["ui_label"] == "FOO"

        payload = editor.set_signal_field("foo", "ui-label", "Risk Level")
        assert payload["name"] == "risk_level"
        assert payload["ui_label"] == "Risk Level"
        names = {s.name for s in builds(editor.serialize()).signals}
        assert "risk_level" in names
        assert "foo" not in names


class TestRenameSignal:
    def test_renames_the_key_in_place_suffixing_collisions(self):
        editor = make_editor()
        payload = editor.rename_signal("foo", "danger")
        assert payload["name"] == "danger"
        assert list(editor._raw["signals"].keys()) == ["danger", "bar"]
        assert {s.name for s in builds(editor.serialize()).signals} == {"danger", "bar"}

        assert make_editor().rename_signal("foo", "bar")["name"] == "bar_2"

    def test_rewrites_only_the_triggers_referencing_the_old_name_via_ast_not_text(self):
        editor = make_editor()
        editor.rename_signal("foo", "danger")
        automaton = builds(editor.serialize())
        assert _action(automaton, "a", "go-b").trigger == "signal.danger >= 50"
        assert _action(automaton, "a", "go-c").trigger == "signal.danger >= 50 and signal.bar >= 50"


class TestDeleteState:
    def test_removes_the_state_and_incoming_actions_but_refuses_the_init_actions_own_target(self):
        editor = make_editor()
        editor.delete_state("c")
        assert "c" not in editor._raw["states"]
        action_names = {a.name for a in builds(editor.serialize()).states["a"].actions}
        assert "go-c" not in action_names
        assert "go-b" in action_names

        with pytest.raises(InitActionTargetError):
            editor.delete_state("a")
        assert "a" in editor._raw["states"]

    def test_outgoing_actions_vanish_along_with_the_state_itself(self):
        editor = make_editor("""\
project:
  id: proj
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
        assert "a" not in builds(editor.serialize()).states


class TestDeleteAction:
    def test_removes_only_that_action_no_cascade(self):
        editor = make_editor()
        editor.delete_action("a", "go-b")
        automaton = builds(editor.serialize())
        assert {a.name for a in automaton.states["a"].actions} == {"go-c"}
        assert "b" in automaton.states


class TestDeleteSignal:
    def test_removes_the_signal_dropping_just_its_operand_from_bool_ops_and_whole_lone_triggers(self):
        editor = make_editor()
        editor.delete_signal("bar")
        assert "bar" not in editor._raw["signals"]
        assert _action(builds(editor.serialize()), "a", "go-c").trigger == "signal.foo >= 50"

        editor.delete_signal("foo")
        assert _action(builds(editor.serialize()), "a", "go-b").trigger is None

    def test_unrelated_triggers_survive_and_a_trigger_referencing_it_everywhere_is_emptied(self):
        editor = make_editor("""\
project:
  id: proj
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
        trigger: signal.bar >= 50
      - name: go-c
        target: b
        trigger: signal.foo >= 50 or signal.foo <= 0
  b:
    ui-label: B
    contextual-prompt: there
""")
        editor.delete_signal("foo")
        automaton = builds(editor.serialize())
        assert _action(automaton, "a", "go-b").trigger == "signal.bar >= 50"
        assert _action(automaton, "a", "go-c").trigger is None
