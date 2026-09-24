"""attachment.<doc_id>.read()/render() — one of this project's own
files directly under `behaviour/`, reached by its name lowercased with
every non-identifier character turned into `_` and no extension. Every
reference is validated at build time."""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from tracking.actuators import MAX_ATTACHMENT_READ_BYTES

pytestmark = pytest.mark.contract


def _project_with_task(task_line: str) -> str:
    return f"""
project:
  id: p
init-action:
  target: a
env:
  reminder_days:
    type: number
states:
  a:
    input-processor: ai
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        task: |
          {task_line}
  b:
    input-processor: ai
    contextual-prompt: there
"""


def _build_task(call: str, archives: dict | None = None):
    return AutomatonBuilder().build({"index.yml": _project_with_task(call), **(archives or {})})


def test_a_file_under_behaviour_is_reached_by_its_name_lowercased_with_every_other_character_an_underscore():
    automaton = _build_task("attachment.template_informe.read()", {"behaviour/Template informe.txt": "be kind"})
    assert automaton.states["a"].actions[0].task.strip() == "attachment.template_informe.read()"


@pytest.mark.parametrize(("call", "archives", "match"), [
    ("attachment.policy.read()", {"policy.txt": "be kind"}, r"undefined name\(s\): attachment.policy"),
    ("attachment.policy.read()", {"behaviour/sub/policy.txt": "be kind"}, r"undefined name\(s\): attachment.policy"),
    ("attachment._2024_plan.read()", {"behaviour/2024 plan.md": "x"}, r"undefined name\(s\): attachment._2024_plan"),
    ("attachment.policy.read()", {"behaviour/Policy.md": "a", "behaviour/policy.txt": "b"}, "rename all but one"),
    ("attachment.logo.read()", {"behaviour/logo.png": b"\x89PNG"}, "binary file"),
    ("attachment.big.read()", {"behaviour/big.txt": "x" * (MAX_ATTACHMENT_READ_BYTES + 1)}, f"over the {MAX_ATTACHMENT_READ_BYTES}-byte limit"),
    ("attachment.policy.read('policy.txt')", {"behaviour/policy.txt": "be kind"}, "too many positional arguments"),
    ("attachment.policy.open()", {"behaviour/policy.txt": "be kind"}, r"undefined name\(s\): attachment.policy.open"),
], ids=[
    "outside-behaviour", "nested-under-behaviour", "starts-with-a-digit", "two-files-one-name",
    "binary", "oversized", "an-argument", "unknown-method",
])
def test_an_attachment_reference_is_refused_unless_it_names_one_readable_text_file_directly_under_behaviour(call, archives, match):
    with pytest.raises(ValueError, match=match):
        _build_task(call, archives)


@pytest.mark.parametrize("field_yaml", [
    '        trigger: "attachment.policy.read() != \'\'"',
], ids=["trigger"])
def test_attachment_may_only_be_referenced_from_a_script(field_yaml):
    content = f"""
project:
  id: p
init-action:
  target: a
env:
  notes:
    type: string
states:
  a:
    input-processor: ai
    contextual-prompt: hi
    actions:
      - name: go
        target: b
{field_yaml}
  b:
    input-processor: ai
    contextual-prompt: there
"""
    with pytest.raises(ValueError, match=r"undefined name\(s\).*attachment.policy"):
        AutomatonBuilder().build({"index.yml": content, "behaviour/policy.txt": "be kind"})


def test_attachment_render_builds_when_every_expression_in_the_file_is_one_the_script_could_write():
    automaton = _build_task(
        "attachment.report.render()", {"behaviour/report.md": "{{ env.reminder_days }} days — 100% {x}"},
    )
    assert automaton.states["a"].actions[0].task.strip() == "attachment.report.render()"


@pytest.mark.parametrize(("template", "match"), [
    ("{{ env.nope }}", "nope"),
    ("{{ 1 + }}", "not a valid expression"),
], ids=["undeclared-env-key-in-file", "malformed-expression-in-file"])
def test_attachment_render_refuses_any_expression_in_the_file_that_would_fail(template, match):
    with pytest.raises(ValueError, match=match):
        _build_task("attachment.report.render()", {"behaviour/report.md": template})
