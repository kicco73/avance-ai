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


def _build_on_enter(call: str, archives: dict | None = None):
    return AutomatonBuilder().build({"index.yml": _project_with_on_enter(call), **(archives or {})})


def test_attachment_read_resolves_an_exact_path_or_a_unique_basename_under_a_subdirectory():
    for archives in ({"policy.txt": "be kind"}, {"behaviour/policy.txt": "be kind"}):
        automaton = _build_on_enter("attachment.read('policy.txt')", archives)
        assert automaton.states["a"].actions[0].on_enter.strip() == "attachment.read('policy.txt')"


@pytest.mark.parametrize(("call", "archives", "match"), [
    ("attachment.read()", {"policy.txt": "be kind"}, "one string literal argument"),
    ("attachment.read('a', 'b')", {"policy.txt": "be kind"}, "one string literal argument"),
    ("attachment.read(name='policy.txt')", {"policy.txt": "be kind"}, "one string literal argument"),
    ("attachment.read(env.reminder_days)", {"policy.txt": "be kind"}, "one string literal argument"),
    ("attachment.read('missing.txt')", None, "not found"),
    ("attachment.read('policy.txt')", {"a/policy.txt": "be kind", "b/policy.txt": "also be kind"}, "ambiguous"),
    ("attachment.read('logo.png')", {"logo.png": b"\x89PNG"}, "binary file"),
    ("attachment.read('big.txt')", {"big.txt": "x" * (MAX_ATTACHMENT_READ_BYTES + 1)}, f"over the {MAX_ATTACHMENT_READ_BYTES}-byte limit"),
], ids=[
    "no-argument", "two-arguments", "keyword-argument", "non-literal",
    "undeclared", "ambiguous-basename", "binary", "oversized",
])
def test_attachment_read_rejects_anything_but_a_string_literal_naming_one_readable_text_archive(call, archives, match):
    with pytest.raises(ValueError, match=match):
        _build_on_enter(call, archives)


@pytest.mark.parametrize("field_yaml", [
    '        trigger: "attachment.read(\'policy.txt\') != \'\'"',
    "        env:\n          notes: attachment.read('policy.txt')",
], ids=["trigger", "env-expression"])
def test_attachment_may_only_be_referenced_from_on_enter(field_yaml):
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
{field_yaml}
  b:
    contextual-prompt: there
"""
    with pytest.raises(ValueError, match=r"undefined name\(s\).*attachment.read"):
        AutomatonBuilder().build({"index.yml": content, "policy.txt": "be kind"})
