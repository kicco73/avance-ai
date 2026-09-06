"""AutomatonYamlEditor — env key/source edits, action reordering and
serialization round-trips (see test_automaton_yaml_editor.py for the
state/signal/action half).
"""
from __future__ import annotations

import pytest

from automaton_yaml_editor_helpers import BASE_YAML, ENV_BASE_YAML, SOURCE_ARCHIVES, SOURCE_BASE_YAML, builds, make_editor

pytestmark = pytest.mark.contract


def _action(automaton, state: str, name: str):
    return next(a for a in automaton.states[state].actions if a.name == name)


class TestAddEnvKey:
    def test_generates_a_unique_valid_identifier_suffixing_collisions_and_still_builds(self):
        editor = make_editor()
        payload = editor.add_env_key()
        assert payload["name"] == "new_env_key"
        assert payload["ui_description"] is None
        assert payload["value"] == ""

        assert editor.add_env_key()["name"] == "new_env_key_2"
        assert any(e.name == payload["name"] for e in builds(editor.serialize()).env_keys)


class TestSetEnvKeyField:
    def test_plain_edits_stay_in_place_and_a_name_changing_the_sanitized_name_renames_the_key(self):
        editor = make_editor(ENV_BASE_YAML)
        payload = editor.set_env_key_field("visits", "ui-description", "Updated description")
        assert payload["name"] == "visits"
        assert payload["ui_description"] == "Updated description"
        assert editor.set_env_key_field("score", "value", "0")["value"] == "0"
        assert editor.set_env_key_field("visits", "name", "visits")["name"] == "visits"

        assert editor.set_env_key_field("visits", "name", "Visit Count")["name"] == "visit_count"
        names = {e.name for e in builds(editor.serialize()).env_keys}
        assert "visit_count" in names
        assert "visits" not in names


class TestRenameEnvKey:
    def test_renames_the_key_in_place_suffixing_collisions(self):
        editor = make_editor(ENV_BASE_YAML)
        payload = editor.rename_env_key("visits", "visit_count")
        assert payload["name"] == "visit_count"
        assert list(editor._raw["env"].keys()) == ["visit_count", "score"]
        assert {e.name for e in builds(editor.serialize()).env_keys} == {"visit_count", "score"}

        assert make_editor(ENV_BASE_YAML).rename_env_key("visits", "score")["name"] == "score_2"

    def test_rewrites_only_the_triggers_referencing_the_old_name_via_ast_not_text(self):
        editor = make_editor(ENV_BASE_YAML)
        editor.rename_env_key("visits", "visit_count")
        automaton = builds(editor.serialize())
        assert _action(automaton, "a", "go-b").trigger == "env.visit_count >= 1"
        assert _action(automaton, "a", "go-c").trigger == "env.visit_count >= 1 and env.score >= 50"


class TestDeleteEnvKey:
    def test_removes_the_key_dropping_just_its_operand_from_bool_ops_and_whole_lone_triggers(self):
        editor = make_editor(ENV_BASE_YAML)
        editor.delete_env_key("score")
        assert "score" not in editor._raw["env"]
        assert _action(builds(editor.serialize()), "a", "go-c").trigger == "env.visits >= 1"

        editor.delete_env_key("visits")
        assert _action(builds(editor.serialize()), "a", "go-b").trigger is None


class TestAddSource:
    def test_generates_a_unique_valid_identifier_suffixing_collisions_and_still_builds(self):
        editor = make_editor()
        payload = editor.add_source()
        assert payload["name"] == "behaviour"
        assert payload["ui_label"] == "behaviour"
        assert payload["ui_description"] is None
        assert payload["url"] == ""

        assert editor.add_source()["name"] == "behaviour1"
        assert editor.add_source()["name"] == "behaviour2"
        assert any(s.name == payload["name"] for s in builds(editor.serialize()).sources)


class TestSetSourceField:
    def test_plain_edits_stay_in_place_and_a_name_changing_the_sanitized_name_renames_the_source(self):
        editor = make_editor(SOURCE_BASE_YAML)
        payload = editor.set_source_field("pino", "ui-description", "Updated description")
        assert payload["name"] == "pino"
        assert payload["ui_description"] == "Updated description"
        assert editor.set_source_field("cities", "url", "avance:flights.csv")["url"] == "avance:flights.csv"

        payload = editor.set_source_field("pino", "ai-definition", "One row per flight.")
        assert payload["ai_definition"] == "One row per flight."
        assert next(s for s in builds(editor.serialize(), SOURCE_ARCHIVES).sources if s.name == "pino").ai_definition == "One row per flight."

        assert editor.set_source_field("pino", "name", "pino")["name"] == "pino"
        assert editor.set_source_field("pino", "name", "Flight Records")["name"] == "flight_records"
        names = {s.name for s in builds(editor.serialize(), SOURCE_ARCHIVES).sources}
        assert "flight_records" in names
        assert "pino" not in names


class TestRenameSource:
    def test_renames_the_source_in_place_suffixing_collisions(self):
        editor = make_editor(SOURCE_BASE_YAML)
        payload = editor.rename_source("pino", "flight_records")
        assert payload["name"] == "flight_records"
        assert list(editor._raw["sources"].keys()) == ["flight_records", "cities"]
        assert {s.name for s in builds(editor.serialize(), SOURCE_ARCHIVES).sources} == {"flight_records", "cities"}

        assert make_editor(SOURCE_BASE_YAML).rename_source("pino", "cities")["name"] == "cities_2"

    def test_rewrites_only_the_triggers_referencing_the_old_name_via_ast_not_text(self):
        editor = make_editor(SOURCE_BASE_YAML)
        editor.rename_source("pino", "flight_records")
        automaton = builds(editor.serialize(), SOURCE_ARCHIVES)
        assert _action(automaton, "a", "go-b").trigger == "source.flight_records.select('x') != 'nope'"
        assert _action(automaton, "a", "go-c").trigger == "source.flight_records.select('x') != 'nope' and source.cities.select('x') != 'nope'"


class TestDeleteSource:
    def test_removes_the_source_dropping_just_its_operand_from_bool_ops_and_whole_lone_triggers(self):
        editor = make_editor(SOURCE_BASE_YAML)
        editor.delete_source("cities")
        assert "cities" not in editor._raw["sources"]
        assert _action(builds(editor.serialize(), SOURCE_ARCHIVES), "a", "go-c").trigger == "source.pino.select('x') != 'nope'"

        editor.delete_source("pino")
        assert _action(builds(editor.serialize(), SOURCE_ARCHIVES), "a", "go-b").trigger is None


class TestReorderActions:
    def test_moves_the_action_to_the_given_position_and_moving_onto_itself_is_a_noop(self):
        editor = make_editor()
        assert [a["name"] for a in editor.reorder_actions("a", "go-b", 0)] == ["go-b", "go-c"]

        assert [a["name"] for a in editor.reorder_actions("a", "go-c", 0)] == ["go-c", "go-b"]
        assert [a.name for a in builds(editor.serialize()).states["a"].actions] == ["go-c", "go-b"]

    def test_unknown_action_name_or_out_of_range_position_raises(self):
        editor = make_editor()
        with pytest.raises(ValueError):
            editor.reorder_actions("a", "does-not-exist", 0)
        with pytest.raises(ValueError):
            editor.reorder_actions("a", "go-b", 5)
        with pytest.raises(ValueError):
            editor.reorder_actions("a", "go-b", -1)


class TestSerializeRoundTrip:
    def test_an_untouched_document_serializes_back_unchanged_and_a_single_edit_touches_one_line(self):
        assert make_editor().serialize() == BASE_YAML

        editor = make_editor()
        editor.set_state_field("b", "ui-label", "Renamed B")
        before_lines = BASE_YAML.splitlines()
        after_lines = editor.serialize().splitlines()
        assert len(before_lines) == len(after_lines)
        diffs = [i for i, (a, b) in enumerate(zip(before_lines, after_lines)) if a != b]
        assert diffs == [before_lines.index("  b:") + 1]
