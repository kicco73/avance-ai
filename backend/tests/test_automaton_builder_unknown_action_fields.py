"""A field an action does not have is a field that never runs, and YAML
gives no sign of it: `on-enter:` carried a live `send_mail` call through
four published revisions of a real project, invisible to everything that
reads an automaton — including the build asking whether mail was
required, which truthfully answered no. Every field an action does not
have is a build error now, whatever it used to mean.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from automaton.build_error import AutomatonBuildError

pytestmark = pytest.mark.contract


_CONTENT = ""


def _build(action_yaml: str):
    global _CONTENT
    content = _CONTENT = f"""
project:
  id: test_project
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: a
{action_yaml}
"""
    return AutomatonBuilder().build({"index.yml": content})


def _warnings(action_yaml: str) -> list[dict]:
    return _build(action_yaml).build_warnings


def test_a_field_an_action_does_not_have_is_rejected_rather_than_ignored():
    with pytest.raises(AutomatonBuildError) as exc_info:
        _build("        whenever: |\n          task.send_mail(user.email, 'hi')\n")

    assert "'whenever' is not a field an action has" in str(exc_info.value)
    assert "on-exit" in str(exc_info.value)


def test_it_says_where_it_found_it_so_the_editor_can_go_there():
    """A rejection nobody can find is a rejection nobody acts on — the
    line is the offending key's own, not the section's."""
    with pytest.raises(AutomatonBuildError) as exc_info:
        _build("        whenever: 1\n")

    assert exc_info.value.section == "states.a.actions.go"
    assert _CONTENT.splitlines()[exc_info.value.line].strip().startswith("whenever:")


def test_a_field_the_format_renamed_is_refused_the_same_way():
    """A build has no memory of what `on-enter` used to be: it is not a
    field an action has, and that is all it can say. Turning it back into
    the `task` it means is the modernizer's job, before a build sees the
    file (test_index_yml_modernizer.py)."""
    with pytest.raises(AutomatonBuildError, match="'on-enter' is not a field an action has"):
        _build("        on-enter: |\n          task.send_mail(user.email, 'hi')\n")


def test_every_field_an_action_really_has_passes_without_a_word():
    warnings = _warnings(
        "        ui-label: Go\n"
        "        ui-button: Go\n"
        "        ui-description: goes\n"
        "        trigger: 'True'\n"
        "        task: |\n          task.prompt('x')\n"
        "        on-exit: |\n          chat.celebrate()\n"
    )

    assert warnings == []
