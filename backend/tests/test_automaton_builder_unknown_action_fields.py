"""A field an action does not have is a field that never runs, and YAML
gives no sign of it: `on-enter:` carried a live `send_mail` call through
four published revisions of a real project, invisible to everything that
reads an automaton — including the build asking whether mail was
required, which truthfully answered no.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract


_CONTENT = ""


def _warnings(action_yaml: str) -> list[dict]:
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
    return AutomatonBuilder().build({"index.yml": content}).build_warnings


def test_a_field_an_action_does_not_have_is_reported_rather_than_ignored():
    warnings = _warnings("        on-enter: |\n          task.send_mail(user.email, 'hi')\n")

    said = [warning["message"] for warning in warnings]
    assert any("'on-enter' is not a field an action has" in message for message in said)
    assert any("'go'" in message and "on-exit" in message for message in said)


def test_it_says_where_it_found_it_so_the_editor_can_go_there():
    """A warning nobody can find is a warning nobody acts on — the line
    is the offending key's own, not the section's (see BuildCursor.warn)."""
    warnings = _warnings("        on-enter: |\n          task.send_mail(user.email, 'hi')\n")

    assert len(warnings) == 1, warnings
    assert warnings[0]["section"] == "states.a.actions.go"
    assert _CONTENT.splitlines()[warnings[0]["line"]].strip().startswith("on-enter:")


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
