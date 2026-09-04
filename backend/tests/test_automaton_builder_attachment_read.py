"""attachment.read(name) — an on-enter-only namespace that returns one of
this project's own archive files' whole text content. Unlike source.*
(grep/select over a file, bounded, usable from a trigger/env: expression
too), attachment.read is a full read, on-enter only, and every call is
validated at build time (see AutomatonBuilder._validate_attachment_read):
`name` must be a string literal resolving — exact path or unique basename
under `behaviour/`, see AutomatonBuilder._extract_required_archives — to a
text archive no bigger than MAX_ATTACHMENT_READ_BYTES.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from tracking.actuators import MAX_ATTACHMENT_READ_BYTES

pytestmark = pytest.mark.contract


def _project_with_on_enter(on_enter_line: str) -> str:
    return f"""
project:
  id: p
init-action:
  target: a
env:
  reminder_days:
    value: 3
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        on-enter: |
          {on_enter_line}
  b:
    contextual-prompt: there
"""


def test_attachment_read_of_a_declared_text_archive_builds_fine():
    content = {"index.yml": _project_with_on_enter("attachment.read('policy.txt')"), "policy.txt": "be kind"}
    automaton = AutomatonBuilder().build(content)
    assert automaton.states["a"].actions[0].on_enter.strip() == "attachment.read('policy.txt')"


def test_attachment_read_resolves_by_unique_basename_under_a_subdirectory():
    content = {
        "index.yml": _project_with_on_enter("attachment.read('policy.txt')"),
        "behaviour/policy.txt": "be kind",
    }
    automaton = AutomatonBuilder().build(content)
    assert automaton.states["a"].actions[0].on_enter.strip() == "attachment.read('policy.txt')"


@pytest.mark.parametrize("call", [
    "attachment.read()",
    "attachment.read('a', 'b')",
    "attachment.read(name='policy.txt')",
    "attachment.read(env.reminder_days)",
])
def test_attachment_read_rejects_anything_but_a_single_string_literal_argument(call):
    content = {"index.yml": _project_with_on_enter(call), "policy.txt": "be kind"}
    with pytest.raises(ValueError, match="one string literal argument"):
        AutomatonBuilder().build(content)


def test_attachment_read_of_an_undeclared_archive_is_rejected():
    content = {"index.yml": _project_with_on_enter("attachment.read('missing.txt')")}
    with pytest.raises(ValueError, match="not found"):
        AutomatonBuilder().build(content)


def test_attachment_read_of_an_ambiguous_basename_is_rejected():
    content = {
        "index.yml": _project_with_on_enter("attachment.read('policy.txt')"),
        "a/policy.txt": "be kind",
        "b/policy.txt": "also be kind",
    }
    with pytest.raises(ValueError, match="ambiguous"):
        AutomatonBuilder().build(content)


def test_attachment_read_of_a_binary_archive_is_rejected_at_build_time():
    content = {"index.yml": _project_with_on_enter("attachment.read('logo.png')"), "logo.png": b"\x89PNG"}
    with pytest.raises(ValueError, match="binary file"):
        AutomatonBuilder().build(content)


def test_attachment_read_of_an_oversized_archive_is_rejected_at_build_time():
    content = {
        "index.yml": _project_with_on_enter("attachment.read('big.txt')"),
        "big.txt": "x" * (MAX_ATTACHMENT_READ_BYTES + 1),
    }
    with pytest.raises(ValueError, match=f"over the {MAX_ATTACHMENT_READ_BYTES}-byte limit"):
        AutomatonBuilder().build(content)


def test_attachment_may_not_be_referenced_in_a_trigger():
    content = f"""
project:
  id: p
init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        trigger: "attachment.read('policy.txt') != ''"
  b:
    contextual-prompt: there
"""
    with pytest.raises(ValueError, match="undefined name\\(s\\).*attachment.read"):
        AutomatonBuilder().build({"index.yml": content, "policy.txt": "be kind"})


def test_attachment_may_not_be_referenced_in_an_env_expression():
    content = f"""
project:
  id: p
init-action:
  target: a
env:
  notes:
    value: ""
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        env:
          notes: attachment.read('policy.txt')
  b:
    contextual-prompt: there
"""
    with pytest.raises(ValueError, match="undefined name\\(s\\).*attachment.read"):
        AutomatonBuilder().build({"index.yml": content, "policy.txt": "be kind"})
