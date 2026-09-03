"""The top-level `project:` section (id/family/revision/ui-label/
ui-description/signal-tracking-on-ai-message, see
AutomatonBuilder._build_project_metadata). `project:` and `project.id`
are both mandatory; `id` must be a valid Python identifier;
cross-project uniqueness is ProjectService's concern; `family` gates
automaton.* visibility (see test_automaton_builder_automaton_namespace.py).
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract

MINIMAL_STATES = "init-action:\n  target: a\nstates:\n  a:\n    contextual-prompt: hi\n"


def _build(project_yaml: str = "project:\n  id: my_project\n") -> object:
    return AutomatonBuilder().build({"index.yml": project_yaml + MINIMAL_STATES})


def test_project_section_is_required():
    with pytest.raises(ValueError, match="'project' is required"):
        AutomatonBuilder().build({"index.yml": MINIMAL_STATES})


def test_signal_tracking_on_ai_message_is_parsed_from_project():
    automaton = _build("project:\n  id: my_project\n  signal-tracking-on-ai-message: true\n")
    assert automaton.autotracking_on_ai_message is True


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


def test_ui_label_and_ui_description_default_to_none_when_absent():
    automaton = _build("project:\n  id: my_project\n")
    assert automaton.project_ui_label is None
    assert automaton.project_ui_description is None


def test_project_must_be_a_mapping():
    with pytest.raises(ValueError, match="must be a mapping"):
        _build("project: not-a-mapping\n")


@pytest.mark.parametrize("bad_id", ["not valid", "1starts_with_digit", "has-hyphen", "has.dot", ""])
def test_rejects_an_id_that_is_not_a_valid_python_identifier(bad_id):
    with pytest.raises(ValueError, match="must be a valid identifier"):
        _build(f"project:\n  id: '{bad_id}'\n")


def test_accepts_underscores_and_digits_not_leading():
    automaton = _build("project:\n  id: _my_project_2\n")
    assert automaton.project_id == "_my_project_2"


def test_family_is_none_when_absent():
    automaton = _build("project:\n  id: my_project\n")
    assert automaton.family is None


def test_family_is_parsed_when_present():
    automaton = _build("project:\n  id: my_project\n  family: my_family\n")
    assert automaton.family == "my_family"


def test_revision_defaults_to_zero():
    automaton = _build("project:\n  id: my_project\n")
    assert automaton.project_revision == 0


def test_revision_is_parsed_when_present():
    automaton = _build("project:\n  id: my_project\n  revision: 5\n")
    assert automaton.project_revision == 5


def test_rejects_a_negative_revision():
    with pytest.raises(ValueError, match="non-negative integer"):
        _build("project:\n  id: my_project\n  revision: -1\n")
