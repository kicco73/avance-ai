"""The top-level `project:` section (parallel to init-action/states/
signals/env, see AutomatonBuilder._build_project_metadata) — id/ui-label/
ui-description. `id` is what *other* projects reach this one as through
automaton.* (see automaton.trigger_automaton_project_refs, Prompt 8/9);
this module covers the YAML-local half of its validation (must be a
valid identifier) — global uniqueness across every project is
ProjectService's own concern (see test_project_id_uniqueness.py).
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract

MINIMAL_STATES = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


def _build(project_yaml: str = "") -> object:
    return AutomatonBuilder().build({"index.yml": project_yaml + MINIMAL_STATES})


def test_project_section_is_entirely_optional():
    automaton = _build()
    assert automaton.project_id is None
    assert automaton.project_ui_label is None
    assert automaton.project_ui_description is None


def test_project_id_ui_label_ui_description_are_parsed():
    automaton = _build(
        "project:\n"
        "  id: my_project\n"
        "  ui-label: My Project\n"
        "  ui-description: A friendly description.\n"
    )
    assert automaton.project_id == "my_project"
    assert automaton.project_ui_label == "My Project"
    assert automaton.project_ui_description == "A friendly description."


def test_ui_label_and_ui_description_work_without_an_id():
    automaton = _build("project:\n  ui-label: My Project\n")
    assert automaton.project_id is None
    assert automaton.project_ui_label == "My Project"


def test_project_must_be_a_mapping():
    with pytest.raises(ValueError, match="'project' must be a mapping"):
        _build("project: not-a-mapping\n")


@pytest.mark.parametrize("bad_id", ["not valid", "1starts_with_digit", "has-hyphen", "has.dot", ""])
def test_rejects_an_id_that_is_not_a_valid_python_identifier(bad_id):
    with pytest.raises(ValueError, match="not a valid identifier"):
        _build(f"project:\n  id: '{bad_id}'\n")


def test_accepts_underscores_and_digits_not_leading():
    automaton = _build("project:\n  id: _my_project_2\n")
    assert automaton.project_id == "_my_project_2"
