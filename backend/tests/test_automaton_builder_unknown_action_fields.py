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


def _warnings(action_yaml: str) -> list[str]:
    content = f"""
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

    assert any("'on-enter' is not a field an action has" in warning for warning in warnings)
    assert any("'go'" in warning and "on-exit" in warning for warning in warnings)


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
