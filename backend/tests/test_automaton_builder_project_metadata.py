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


def test_every_project_field_is_parsed_with_its_default_when_absent():
    defaults = _build()
    assert defaults.project_id == "my_project"
    assert defaults.project_ui_label is None
    assert defaults.project_ui_description is None
    assert defaults.family is None
    assert defaults.project_revision == 0
    assert defaults.autotracking_on_ai_message is False

    full = _build(
        "project:\n"
        "  id: _my_project_2\n"
        "  family: my_family\n"
        "  revision: 5\n"
        "  ui-label: My Project\n"
        "  ui-description: A friendly description.\n"
        "  signal-tracking-on-ai-message: true\n"
    )
    assert full.project_id == "_my_project_2"
    assert full.family == "my_family"
    assert full.project_revision == 5
    assert full.project_ui_label == "My Project"
    assert full.project_ui_description == "A friendly description."
    assert full.autotracking_on_ai_message is True


def test_the_project_section_is_required_must_be_a_mapping_and_its_revision_non_negative():
    with pytest.raises(ValueError, match="'project' is required"):
        AutomatonBuilder().build({"index.yml": MINIMAL_STATES})
    with pytest.raises(ValueError, match="must be a mapping"):
        _build("project: not-a-mapping\n")
    with pytest.raises(ValueError, match="non-negative integer"):
        _build("project:\n  id: my_project\n  revision: -1\n")


@pytest.mark.parametrize("bad_id", ["not valid", "1starts_with_digit", "has-hyphen", "has.dot", ""])
def test_rejects_an_id_that_is_not_a_valid_python_identifier(bad_id):
    with pytest.raises(ValueError, match="must be a valid identifier"):
        _build(f"project:\n  id: '{bad_id}'\n")
