"""Every section of index.yml only accepts the fields it reads. A name
nothing reads is a name that never runs, and YAML gives no sign of it —
`chat: false` sat in a published project on 83 states, each of them
answering the user it was written to silence.

A build knows the fields it reads and nothing else — not what any of
them used to be called. Every other name is refused, whether it is a
typo or a spelling the format moved past, and the one thing that knows
the difference is the modernizer, which rewrites what it can before a
build ever sees the file (see test_index_yml_modernizer.py).
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from automaton.build_error import AutomatonBuildError

pytestmark = pytest.mark.contract

BASE = """\
project:
  id: strict
{project}
init-action:
  target: a
{top}
signals:
  mood:
    definition: How they feel.
{signal}
reactions:
  wave:
    definition: A wave.
{reaction}
env:
  counter:
    value: "0"
{env}
sources:
  book:
    url: "avance:book.csv"
{source}
states:
  a:
    contextual-prompt: hi
{state}
"""


def _build(**sections):
    blank = {key: "" for key in ("project", "top", "signal", "reaction", "env", "source", "state")}
    return AutomatonBuilder().build(
        {"index.yml": BASE.format(**{**blank, **sections}), "book.csv": "a,b\n1,2\n"}
    )


def test_the_sections_of_a_clean_project_pass_without_a_word():
    assert _build().build_warnings == []


@pytest.mark.parametrize("section,yaml,noun", [
    ("project", "  whatever: 1", "project"),
    ("top", "whatever: 1", "top-level"),
    ("signal", "    whatever: 1", "Signal"),
    ("reaction", "    whatever: 1", "Reaction"),
    ("env", "    whatever: 1", "Env key"),
    ("source", "    whatever: 1", "Source"),
    ("state", "    whatever: 1", "State"),
])
def test_a_field_a_section_does_not_have_is_rejected_and_says_what_it_could_have_been(section, yaml, noun):
    with pytest.raises(AutomatonBuildError) as exc_info:
        _build(**{section: yaml})

    message = str(exc_info.value)
    assert "whatever" in message and noun in message
    assert "expected one of" in message


def test_a_top_level_actions_library_is_allowed_because_anchors_live_there():
    """A project can keep its actions as anchors under a top-level
    `actions:` and merge them into states — the builder reads nothing
    there, but rejecting it would break a legitimate way to write the
    file (see automaton/deprecations.actions_in)."""
    assert _build(top="actions:\n  - &noop\n    name: noop\n").build_warnings == []


@pytest.mark.parametrize("section,yaml,field", [
    ("state", "    on-enter: task.prompt('x')", "on-enter"),
    ("state", "    chat: false", "chat"),
    ("env", "    ai-access: readonly", "ai-access"),
    ("project", "  talk-enabled: true", "talk-enabled"),
])
def test_a_spelling_the_format_moved_past_is_refused_like_any_other(section, yaml, field):
    """A build has no memory of what a field used to be called, so it
    cannot treat one of these more gently than a typo — and does not
    need to: they never reach it, because opening the project rewrites
    them first."""
    with pytest.raises(AutomatonBuildError) as exc_info:
        _build(**{section: yaml})

    assert f"'{field}'" in str(exc_info.value) or f".{field}" in str(exc_info.value)


def test_one_pass_reports_every_problem_it_found_rather_than_the_first():
    """A build that stops at the first bad field costs a whole pass per
    mistake — five mistakes, five rounds of fixing and rebuilding. Every
    field a pass can carry on in spite of is collected and reported
    together (see BuildCursor.reject)."""
    with pytest.raises(AutomatonBuildError) as exc_info:
        _build(signal="    whatever: 1", state="    whichever: 2", env="    whenever: 3")

    assert str(exc_info.value) == "3 problems in index.yml"
    said = " ".join(problem["message"] for problem in exc_info.value.problems)
    assert "whatever" in said and "whichever" in said and "whenever" in said


def test_a_single_problem_is_said_on_its_own_without_a_preamble():
    with pytest.raises(AutomatonBuildError) as exc_info:
        _build(signal="    whatever: 1")

    assert str(exc_info.value).startswith("Signal 'mood': 'whatever' is not a field a signal has")
