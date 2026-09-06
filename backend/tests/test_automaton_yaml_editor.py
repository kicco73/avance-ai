"""AutomatonYamlEditor — state/signal/action/init-action edits against a
project's index.yml, working directly on a ruamel.yaml round-trip tree.
These tests check the structural edit itself, not end-to-end validity.
"""
from __future__ import annotations

import pytest

from automaton.automaton_yaml_editor import AutomatonYamlEditor, InitActionTargetError
from automaton_yaml_editor_helpers import BASE_YAML, SOURCE_ARCHIVES, SOURCE_BASE_YAML, builds, make_editor

pytestmark = pytest.mark.contract


class TestToSnakeCase:
    def test_lowercases_and_replaces_non_alnum_with_underscore(self):
        assert AutomatonYamlEditor.to_snake_case("Risk Level!") == "risk_level"

    def test_collapses_repeated_separators(self):
        assert AutomatonYamlEditor.to_snake_case("Risk   Level -- High") == "risk_level_high"

    def test_trims_leading_and_trailing_underscores(self):
        assert AutomatonYamlEditor.to_snake_case("--Risk--") == "risk"


class TestAddState:
    def test_generates_a_numbered_name_and_placeholder_label(self):
        editor = make_editor()
        payload = editor.add_state()

        assert payload["key"] == "state-0"
        assert payload["ui_label"] == "New State"
        assert payload["ui_description"] is None
        assert payload["final"] is True
        assert payload["chat"] is True
        assert payload["actions"] == []

    def test_numbering_continues_from_the_highest_existing_state_n(self):
        editor = make_editor(BASE_YAML + "  state-0:\n    ui-label: Pre-existing\n    contextual-prompt: x\n")
        payload = editor.add_state()
        assert payload["key"] == "state-1"

    def test_ui_label_collision_gets_a_numeric_suffix(self):
        editor = make_editor()
        editor.add_state()  # "New State"
        second = editor.add_state()
        assert second["ui_label"] == "New State 2"

    def test_result_still_builds_and_is_reachable(self):
        editor = make_editor()
        payload = editor.add_state()
        text = editor.serialize()

        automaton = builds(text)
        assert payload["key"] in automaton.states


class TestAddSignal:
    def test_name_is_derived_from_the_generated_ui_label(self):
        editor = make_editor()
        payload = editor.add_signal()

        assert payload["ui_label"] == "New Signal"
        assert payload["name"] == "new_signal"
        assert payload["definition"] == ""
        assert payload["attachments"] == {}
        assert payload["error"] is None

    def test_ui_label_and_name_collisions_both_get_suffixed(self):
        editor = make_editor()
        editor.add_signal()  # "New Signal" / new_signal
        second = editor.add_signal()
        assert second["ui_label"] == "New Signal 2"
        assert second["name"] == "new_signal_2"

    def test_result_stillbuilds(self):
        editor = make_editor()
        payload = editor.add_signal()
        automaton = builds(editor.serialize())
        assert any(s.name == payload["name"] for s in automaton.signals)


class TestAddAction:
    def test_generates_a_numbered_name_scoped_to_the_state(self):
        editor = make_editor()
        payload = editor.add_action("b")

        assert payload["name"] == "action-0"
        assert payload["ui_label"] == "New Action"
        assert payload["ui_button"] == "New Action"  # falls back through ui-label
        assert payload["ui_description"] is None
        assert payload["target"] == "b"  # self-loop, target omitted
        assert payload["has_trigger"] is False
        assert payload["on-enter"] is None

    def test_numbering_is_scoped_to_the_state_not_the_project(self):
        editor = make_editor()
        editor.add_action("a")  # state "a" already has go-b/go-c, no action-N yet -> action-0
        first_b = editor.add_action("b")
        assert first_b["name"] == "action-0"  # state "b" has its own independent counter

    def test_same_ui_label_allowed_in_a_different_state(self):
        editor = make_editor()
        editor.add_action("b")  # "New Action"
        other_state = editor.add_action("c")
        assert other_state["ui_label"] == "New Action"  # no suffix — different state's own scope

    def test_ui_label_collision_within_the_same_state_gets_suffixed(self):
        editor = make_editor()
        editor.add_action("b")
        second = editor.add_action("b")
        assert second["ui_label"] == "New Action 2"

    def test_result_stillbuilds(self):
        editor = make_editor()
        payload = editor.add_action("b")
        automaton = builds(editor.serialize())
        assert any(a.name == payload["name"] for a in automaton.states["b"].actions)


class TestSetStateField:
    def test_ui_label_edit_never_touches_the_key(self):
        editor = make_editor()
        payload = editor.set_state_field("a", "ui-label", "Renamed A")

        assert payload["key"] == "a"
        assert payload["ui_label"] == "Renamed A"
        automaton = builds(editor.serialize())
        assert "a" in automaton.states
        assert automaton.states["a"].ui_label == "Renamed A"

    def test_history_cutoff_edit(self):
        editor = make_editor()
        payload = editor.set_state_field("a", "history-cutoff", True)
        assert builds(editor.serialize()).states["a"].history_cutoff is True
        # StatePayload doesn't carry history_cutoff — only confirm no error and a real State result.
        assert payload["key"] == "a"

    def test_contextual_prompt_edit(self):
        editor = make_editor()
        editor.set_state_field("a", "contextual-prompt", "brand new prompt")
        assert builds(editor.serialize()).states["a"].contextual_prompt == "brand new prompt"

    def test_ai_may_read_sources_edit(self):
        editor = make_editor(SOURCE_BASE_YAML)
        editor.set_source_field("pino", "ai-definition", "One row per flight.")
        payload = editor.set_state_field("a", "ai-may-read-sources", ["pino"])

        assert payload["ai_may_read_sources"] == ["pino"]
        assert payload["ai_must_read_sources"] == []
        assert payload["ai_may_write_sources"] == []
        automaton = builds(editor.serialize(), SOURCE_ARCHIVES)
        assert automaton.states["a"].ai_may_read_sources == ("pino",)
        assert automaton.states["a"].ai_must_read_sources == ()

    def test_ai_must_read_sources_edit(self):
        editor = make_editor(SOURCE_BASE_YAML)
        editor.set_source_field("pino", "ai-definition", "One row per flight.")
        payload = editor.set_state_field("a", "ai-must-read-sources", ["pino"])

        assert payload["ai_must_read_sources"] == ["pino"]
        automaton = builds(editor.serialize(), SOURCE_ARCHIVES)
        assert automaton.states["a"].ai_must_read_sources == ("pino",)

    def test_ai_may_write_sources_edit_round_trips_through_the_payload(self):
        editor = make_editor(SOURCE_BASE_YAML)
        payload = editor.set_state_field("a", "ai-may-write-sources", ["pino"])

        assert payload["ai_may_write_sources"] == ["pino"]
        assert "ai-may-write-sources: [pino]" in editor.serialize() or "- pino" in editor.serialize()


class TestSetActionField:
    def test_ui_label_edit_never_touches_the_name(self):
        editor = make_editor()
        payload = editor.set_action_field("a", "go-b", "ui-label", "Renamed")

        assert payload["name"] == "go-b"
        assert payload["ui_label"] == "Renamed"

    def test_target_edit(self):
        editor = make_editor()
        payload = editor.set_action_field("a", "go-b", "target", "c")
        assert payload["target"] == "c"
        automaton = builds(editor.serialize())
        action = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        assert action.target == "c"

    def test_on_enter_edit(self):
        editor = make_editor()
        editor.set_action_field("a", "go-b", "on-enter", "actuator.celebrate()")
        automaton = builds(editor.serialize())
        action = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        assert action.on_enter == "actuator.celebrate()"

    def test_ui_description_edit(self):
        editor = make_editor()
        payload = editor.set_action_field("a", "go-b", "ui-description", "Goes to B.")
        assert payload["ui_description"] == "Goes to B."
        automaton = builds(editor.serialize())
        action = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        assert action.ui_description == "Goes to B."

    def test_trigger_edit(self):
        editor = make_editor()
        payload = editor.set_action_field("a", "go-c", "trigger", "signal.foo < 10")
        assert payload["has_trigger"] is True
        automaton = builds(editor.serialize())
        action = next(a for a in automaton.states["a"].actions if a.name == "go-c")
        assert action.trigger == "signal.foo < 10"

    def test_clearing_trigger_removes_the_key_instead_of_storing_an_empty_string(self):
        editor = make_editor()  # go-b already has "trigger: signal.foo >= 50"
        payload = editor.set_action_field("a", "go-b", "trigger", "")
        assert payload["has_trigger"] is False
        automaton = builds(editor.serialize())
        action = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        assert action.trigger is None

    def test_env_edit(self):
        editor = make_editor(BASE_YAML + "env:\n  counter: {}\n")
        editor.set_action_field("a", "go-b", "env", {"counter": "1"})
        automaton = builds(editor.serialize())
        action = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        assert action.env == {"counter": "1"}

    def test_clearing_env_removes_the_key_instead_of_storing_an_empty_mapping(self):
        editor = make_editor(BASE_YAML + "env:\n  counter: {}\n")
        editor.set_action_field("a", "go-b", "env", {"counter": "1"})
        editor.set_action_field("a", "go-b", "env", {})
        automaton = builds(editor.serialize())
        action = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        assert action.env is None


class TestSetInitActionField:
    """The init-action is an action like any other — same payload shape
    as TestSetActionField above, built off the same _action_payload_from_raw."""

    def test_ui_description_edit(self):
        editor = make_editor()
        payload = editor.set_init_action_field("ui-description", "Where every session begins.")
        assert payload["name"] == "init-action"
        assert payload["ui_description"] == "Where every session begins."
        automaton = builds(editor.serialize())
        assert automaton.init_action.ui_description == "Where every session begins."

    def test_ui_button_falls_back_to_ui_label_like_a_real_action_does(self):
        editor = make_editor()
        payload = editor.set_init_action_field("ui-label", "Begin")
        assert payload["ui_button"] == "Begin"

    def test_has_trigger_is_always_false_even_with_a_stray_trigger_key(self):
        """AutomatonBuilder's own _build_init_action never reads
        'trigger' for the init-action — a stray key left in the YAML
        (however it got there) must never make has_trigger lie about that."""
        stray_trigger_yaml = BASE_YAML.replace("init-action:\n  target: a\n", "init-action:\n  target: a\n  trigger: 'True'\n")
        editor = make_editor(stray_trigger_yaml)
        payload = editor.set_init_action_field("ui-label", "Begin")
        assert payload["has_trigger"] is False


class TestSetSignalField:
    def test_non_ui_label_field_is_a_plain_edit(self):
        editor = make_editor()
        payload = editor.set_signal_field("foo", "definition", "new definition")
        assert payload["name"] == "foo"
        assert payload["definition"] == "new definition"

    def test_ui_label_edit_that_does_not_change_the_derived_name_stays_in_place(self):
        editor = make_editor()
        payload = editor.set_signal_field("foo", "ui-label", "FOO")  # snake_case("FOO") == "foo"
        assert payload["name"] == "foo"
        assert payload["ui_label"] == "FOO"

    def test_ui_label_edit_that_changes_the_derived_name_renames_the_signal(self):
        editor = make_editor()
        payload = editor.set_signal_field("foo", "ui-label", "Risk Level")
        assert payload["name"] == "risk_level"
        assert payload["ui_label"] == "Risk Level"
        automaton = builds(editor.serialize())
        assert "risk_level" in {s.name for s in automaton.signals}
        assert "foo" not in {s.name for s in automaton.signals}


class TestRenameSignal:
    def test_renames_the_key_and_returns_the_updated_payload(self):
        editor = make_editor()
        payload = editor.rename_signal("foo", "danger")
        assert payload["name"] == "danger"
        automaton = builds(editor.serialize())
        assert {s.name for s in automaton.signals} == {"danger", "bar"}

    def test_collision_with_an_existing_signal_name_gets_suffixed(self):
        editor = make_editor()
        payload = editor.rename_signal("foo", "bar")
        assert payload["name"] == "bar_2"

    def test_preserves_the_other_signals_own_order(self):
        editor = make_editor()
        editor.rename_signal("foo", "danger")
        names = list(editor._raw["signals"].keys())
        assert names == ["danger", "bar"]

    def test_rewrites_every_trigger_referencing_the_old_name_via_ast_not_text(self):
        editor = make_editor()
        editor.rename_signal("foo", "danger")
        automaton = builds(editor.serialize())
        go_b = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        go_c = next(a for a in automaton.states["a"].actions if a.name == "go-c")
        assert go_b.trigger == "signal.danger >= 50"
        assert go_c.trigger == "signal.danger >= 50 and signal.bar >= 50"

    def test_a_trigger_not_referencing_the_signal_is_left_untouched(self):
        editor = make_editor()
        editor.rename_signal("foo", "danger")
        automaton = builds(editor.serialize())
        # go-c's own "signal.bar >= 50" clause survives unchanged (bar was never renamed).
        go_c = next(a for a in automaton.states["a"].actions if a.name == "go-c")
        assert "signal.bar >= 50" in go_c.trigger


class TestDeleteState:
    def test_removes_the_state(self):
        editor = make_editor()
        editor.delete_state("c")
        assert "c" not in editor._raw["states"]

    def test_removes_incoming_actions_from_other_states(self):
        editor = make_editor()
        editor.delete_state("c")
        automaton = builds(editor.serialize())
        action_names = {a.name for a in automaton.states["a"].actions}
        assert "go-c" not in action_names
        assert "go-b" in action_names

    def test_outgoing_actions_vanish_along_with_the_state_itself(self):
        # State "a" owns go-b/go-c — deleting "a" removes them implicitly,
        # not via any special-cased cascade of its own.
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
        automaton = builds(editor.serialize())
        assert "a" not in automaton.states

    def test_refuses_to_delete_the_init_actions_own_target(self):
        editor = make_editor()
        with pytest.raises(InitActionTargetError):
            editor.delete_state("a")
        # Left untouched.
        assert "a" in editor._raw["states"]


class TestDeleteAction:
    def test_removes_only_that_action_no_cascade(self):
        editor = make_editor()
        editor.delete_action("a", "go-b")
        automaton = builds(editor.serialize())
        action_names = {a.name for a in automaton.states["a"].actions}
        assert action_names == {"go-c"}
        assert "b" in automaton.states  # state "b" itself untouched


class TestDeleteSignal:
    def test_removes_the_signal_itself(self):
        editor = make_editor()
        editor.delete_signal("bar")
        assert "bar" not in editor._raw["signals"]

    def test_bool_op_drops_just_the_referencing_operand_when_others_survive(self):
        editor = make_editor()
        editor.delete_signal("bar")
        automaton = builds(editor.serialize())
        go_c = next(a for a in automaton.states["a"].actions if a.name == "go-c")
        # "signal.foo >= 50 and signal.bar >= 50" loses only its own "signal.bar >= 50" operand.
        assert go_c.trigger == "signal.foo >= 50"

    def test_a_lone_non_bool_op_trigger_is_removed_entirely_when_it_references_the_signal(self):
        editor = make_editor()
        editor.delete_signal("foo")
        automaton = builds(editor.serialize())
        go_b = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        assert go_b.trigger is None  # action survives, now manual-only

    def test_a_trigger_not_referencing_the_deleted_signal_is_untouched(self):
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
  b:
    ui-label: B
    contextual-prompt: there
""")
        editor.delete_signal("foo")
        automaton = builds(editor.serialize())
        go_b = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        assert go_b.trigger == "signal.bar >= 50"

    def test_all_operands_referencing_the_signal_empties_the_trigger_field(self):
        editor = make_editor("""\
project:
  id: proj
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
        trigger: signal.foo >= 50 or signal.foo <= 0
  b:
    ui-label: B
    contextual-prompt: there
""")
        editor.delete_signal("foo")
        automaton = builds(editor.serialize())
        go_b = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        assert go_b.trigger is None


ENV_BASE_YAML = """\
project:
  id: proj
init-action:
  target: a
env:
  visits:
    ui-description: Visit counter
  score: {}
states:
  a:
    ui-label: State A
    contextual-prompt: hi
    actions:
      - name: go-b
        ui-label: Go to B
        target: b
        trigger: env.visits >= 1
      - name: go-c
        ui-label: Go to C
        target: c
        trigger: env.visits >= 1 and env.score >= 50
  b:
    ui-label: State B
    contextual-prompt: there
  c:
    ui-label: State C
    contextual-prompt: elsewhere
"""
