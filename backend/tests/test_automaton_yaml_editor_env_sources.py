"""AutomatonYamlEditor — env key/source edits, action reordering and
serialization round-trips (see test_automaton_yaml_editor.py for the
state/signal/action half).
"""
from __future__ import annotations

import pytest

from automaton_yaml_editor_helpers import BASE_YAML, ENV_BASE_YAML, SOURCE_ARCHIVES, SOURCE_BASE_YAML, builds, make_editor

pytestmark = pytest.mark.contract


class TestAddEnvKey:
    def test_generates_a_unique_valid_identifier_name(self):
        editor = make_editor()
        payload = editor.add_env_key()

        assert payload["name"] == "new_env_key"
        assert payload["ui_description"] is None
        assert payload["value"] == ""

    def test_name_collisions_get_suffixed(self):
        editor = make_editor()
        editor.add_env_key()  # new_env_key
        second = editor.add_env_key()
        assert second["name"] == "new_env_key_2"

    def test_result_stillbuilds(self):
        editor = make_editor()
        payload = editor.add_env_key()
        automaton = builds(editor.serialize())
        assert any(e.name == payload["name"] for e in automaton.env_keys)


class TestSetEnvKeyField:
    def test_non_name_field_is_a_plain_edit(self):
        editor = make_editor(ENV_BASE_YAML)
        payload = editor.set_env_key_field("visits", "ui-description", "Updated description")
        assert payload["name"] == "visits"
        assert payload["ui_description"] == "Updated description"

    def test_value_field_is_a_plain_edit(self):
        editor = make_editor(ENV_BASE_YAML)
        payload = editor.set_env_key_field("score", "value", "0")
        assert payload["value"] == "0"

    def test_name_edit_that_does_not_change_the_sanitized_name_stays_in_place(self):
        editor = make_editor(ENV_BASE_YAML)
        payload = editor.set_env_key_field("visits", "name", "visits")  # to_snake_case("visits") == "visits"
        assert payload["name"] == "visits"

    def test_name_edit_that_changes_the_sanitized_name_renames_the_key(self):
        editor = make_editor(ENV_BASE_YAML)
        payload = editor.set_env_key_field("visits", "name", "Visit Count")
        assert payload["name"] == "visit_count"
        automaton = builds(editor.serialize())
        assert "visit_count" in {e.name for e in automaton.env_keys}
        assert "visits" not in {e.name for e in automaton.env_keys}


class TestRenameEnvKey:
    def test_renames_the_key_and_returns_the_updated_payload(self):
        editor = make_editor(ENV_BASE_YAML)
        payload = editor.rename_env_key("visits", "visit_count")
        assert payload["name"] == "visit_count"
        automaton = builds(editor.serialize())
        assert {e.name for e in automaton.env_keys} == {"visit_count", "score"}

    def test_collision_with_an_existing_env_key_name_gets_suffixed(self):
        editor = make_editor(ENV_BASE_YAML)
        payload = editor.rename_env_key("visits", "score")
        assert payload["name"] == "score_2"

    def test_preserves_the_other_env_keys_own_order(self):
        editor = make_editor(ENV_BASE_YAML)
        editor.rename_env_key("visits", "visit_count")
        names = list(editor._raw["env"].keys())
        assert names == ["visit_count", "score"]

    def test_rewrites_every_trigger_referencing_the_old_name_via_ast_not_text(self):
        editor = make_editor(ENV_BASE_YAML)
        editor.rename_env_key("visits", "visit_count")
        automaton = builds(editor.serialize())
        go_b = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        go_c = next(a for a in automaton.states["a"].actions if a.name == "go-c")
        assert go_b.trigger == "env.visit_count >= 1"
        assert go_c.trigger == "env.visit_count >= 1 and env.score >= 50"

    def test_a_trigger_not_referencing_the_env_key_is_left_untouched(self):
        editor = make_editor(ENV_BASE_YAML)
        editor.rename_env_key("visits", "visit_count")
        automaton = builds(editor.serialize())
        go_c = next(a for a in automaton.states["a"].actions if a.name == "go-c")
        assert "env.score >= 50" in go_c.trigger


class TestDeleteEnvKey:
    def test_removes_the_env_key_itself(self):
        editor = make_editor(ENV_BASE_YAML)
        editor.delete_env_key("score")
        assert "score" not in editor._raw["env"]

    def test_bool_op_drops_just_the_referencing_operand_when_others_survive(self):
        editor = make_editor(ENV_BASE_YAML)
        editor.delete_env_key("score")
        automaton = builds(editor.serialize())
        go_c = next(a for a in automaton.states["a"].actions if a.name == "go-c")
        assert go_c.trigger == "env.visits >= 1"

    def test_a_lone_non_bool_op_trigger_is_removed_entirely_when_it_references_the_env_key(self):
        editor = make_editor(ENV_BASE_YAML)
        editor.delete_env_key("visits")
        automaton = builds(editor.serialize())
        go_b = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        assert go_b.trigger is None  # action survives, now manual-only


class TestAddSource:
    def test_generates_a_unique_valid_identifier_name(self):
        editor = make_editor()
        payload = editor.add_source()

        assert payload["name"] == "behaviour"
        assert payload["ui_label"] == "behaviour"
        assert payload["ui_description"] is None
        assert payload["url"] == ""

    def test_name_collisions_get_suffixed(self):
        editor = make_editor()
        editor.add_source()  # behaviour
        second = editor.add_source()
        assert second["name"] == "behaviour1"
        third = editor.add_source()
        assert third["name"] == "behaviour2"

    def test_result_stillbuilds(self):
        editor = make_editor()
        payload = editor.add_source()
        automaton = builds(editor.serialize())
        assert any(s.name == payload["name"] for s in automaton.sources)


class TestSetSourceField:
    def test_non_name_field_is_a_plain_edit(self):
        editor = make_editor(SOURCE_BASE_YAML)
        payload = editor.set_source_field("pino", "ui-description", "Updated description")
        assert payload["name"] == "pino"
        assert payload["ui_description"] == "Updated description"

    def test_url_field_is_a_plain_edit(self):
        editor = make_editor(SOURCE_BASE_YAML)
        payload = editor.set_source_field("cities", "url", "avance:flights.csv")
        assert payload["url"] == "avance:flights.csv"

    def test_ai_definition_field_is_a_plain_edit(self):
        editor = make_editor(SOURCE_BASE_YAML)
        payload = editor.set_source_field("pino", "ai-definition", "One row per flight.")
        assert payload["ai_definition"] == "One row per flight."
        assert payload["ui_description"] is None
        automaton = builds(editor.serialize(), SOURCE_ARCHIVES)
        assert next(s for s in automaton.sources if s.name == "pino").ai_definition == "One row per flight."

    def test_name_edit_that_does_not_change_the_sanitized_name_stays_in_place(self):
        editor = make_editor(SOURCE_BASE_YAML)
        payload = editor.set_source_field("pino", "name", "pino")
        assert payload["name"] == "pino"

    def test_name_edit_that_changes_the_sanitized_name_renames_the_source(self):
        editor = make_editor(SOURCE_BASE_YAML)
        payload = editor.set_source_field("pino", "name", "Flight Records")
        assert payload["name"] == "flight_records"
        automaton = builds(editor.serialize(), SOURCE_ARCHIVES)
        assert "flight_records" in {s.name for s in automaton.sources}
        assert "pino" not in {s.name for s in automaton.sources}


class TestRenameSource:
    def test_renames_the_source_and_returns_the_updated_payload(self):
        editor = make_editor(SOURCE_BASE_YAML)
        payload = editor.rename_source("pino", "flight_records")
        assert payload["name"] == "flight_records"
        automaton = builds(editor.serialize(), SOURCE_ARCHIVES)
        assert {s.name for s in automaton.sources} == {"flight_records", "cities"}

    def test_collision_with_an_existing_source_name_gets_suffixed(self):
        editor = make_editor(SOURCE_BASE_YAML)
        payload = editor.rename_source("pino", "cities")
        assert payload["name"] == "cities_2"

    def test_preserves_the_other_sources_own_order(self):
        editor = make_editor(SOURCE_BASE_YAML)
        editor.rename_source("pino", "flight_records")
        names = list(editor._raw["sources"].keys())
        assert names == ["flight_records", "cities"]

    def test_rewrites_every_trigger_referencing_the_old_name_via_ast_not_text(self):
        editor = make_editor(SOURCE_BASE_YAML)
        editor.rename_source("pino", "flight_records")
        automaton = builds(editor.serialize(), SOURCE_ARCHIVES)
        go_b = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        go_c = next(a for a in automaton.states["a"].actions if a.name == "go-c")
        assert go_b.trigger == "source.flight_records.select('x') != 'nope'"
        assert go_c.trigger == "source.flight_records.select('x') != 'nope' and source.cities.select('x') != 'nope'"

    def test_a_trigger_not_referencing_the_source_is_left_untouched(self):
        editor = make_editor(SOURCE_BASE_YAML)
        editor.rename_source("pino", "flight_records")
        automaton = builds(editor.serialize(), SOURCE_ARCHIVES)
        go_c = next(a for a in automaton.states["a"].actions if a.name == "go-c")
        assert "source.cities.select('x') != 'nope'" in go_c.trigger


class TestDeleteSource:
    def test_removes_the_source_itself(self):
        editor = make_editor(SOURCE_BASE_YAML)
        editor.delete_source("cities")
        assert "cities" not in editor._raw["sources"]

    def test_bool_op_drops_just_the_referencing_operand_when_others_survive(self):
        editor = make_editor(SOURCE_BASE_YAML)
        editor.delete_source("cities")
        automaton = builds(editor.serialize(), SOURCE_ARCHIVES)
        go_c = next(a for a in automaton.states["a"].actions if a.name == "go-c")
        assert go_c.trigger == "source.pino.select('x') != 'nope'"

    def test_a_lone_non_bool_op_trigger_is_removed_entirely_when_it_references_the_source(self):
        editor = make_editor(SOURCE_BASE_YAML)
        editor.delete_source("pino")
        automaton = builds(editor.serialize(), SOURCE_ARCHIVES)
        go_b = next(a for a in automaton.states["a"].actions if a.name == "go-b")
        assert go_b.trigger is None  # action survives, now manual-only


class TestReorderActions:
    def test_moves_the_action_to_the_given_position(self):
        editor = make_editor()
        payload = editor.reorder_actions("a", "go-c", 0)
        assert [a["name"] for a in payload] == ["go-c", "go-b"]

    def test_result_reflects_in_the_serialized_yaml(self):
        editor = make_editor()
        editor.reorder_actions("a", "go-c", 0)
        automaton = builds(editor.serialize())
        assert [a.name for a in automaton.states["a"].actions] == ["go-c", "go-b"]

    def test_moving_to_its_own_current_position_is_a_noop(self):
        editor = make_editor()
        payload = editor.reorder_actions("a", "go-b", 0)
        assert [a["name"] for a in payload] == ["go-b", "go-c"]

    def test_unknown_action_name_raises(self):
        editor = make_editor()
        with pytest.raises(ValueError):
            editor.reorder_actions("a", "does-not-exist", 0)

    def test_out_of_range_position_raises(self):
        editor = make_editor()
        with pytest.raises(ValueError):
            editor.reorder_actions("a", "go-b", 5)
        with pytest.raises(ValueError):
            editor.reorder_actions("a", "go-b", -1)


class TestSerializeRoundTrip:
    def test_untouched_document_serializes_back_unchanged(self):
        editor = make_editor()
        assert editor.serialize() == BASE_YAML

    def test_a_single_field_edit_leaves_everything_else_byte_identical(self):
        editor = make_editor()
        editor.set_state_field("b", "ui-label", "Renamed B")
        result = editor.serialize()
        # Every other line survives untouched — only "State B" -> "Renamed B" differs.
        before_lines = BASE_YAML.splitlines()
        after_lines = result.splitlines()
        assert len(before_lines) == len(after_lines)
        diffs = [i for i, (a, b) in enumerate(zip(before_lines, after_lines)) if a != b]
        assert diffs == [before_lines.index("  b:") + 1]
