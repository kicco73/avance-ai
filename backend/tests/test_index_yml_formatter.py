from __future__ import annotations

from automaton.index_yml_formatter import IndexYmlFormatter

FORMATTED = """\
project:
  id: formatted

init-action:
  target: a
  on-exit: |-
    env.x = 1

    env.y = 2

states:
  a:
    input-processor: ai
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        trigger: >-
          (env.x > 0 and
          (env.y < 3 or
          signal.mood > 50))
      - name: stay
        trigger: "True"

  # the last one
  b:
    input-processor: ai
    contextual-prompt: bye
"""

UNFORMATTED = """\
project:
  id: formatted
init-action:
  target: a
  on-exit: "env.x = 1\\n\\nenv.y = 2"


states:
  a:
    input-processor: ai
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        trigger: env.x > 0 and (env.y < 3 or signal.mood > 50)
      - name: stay
        trigger: "True"
  # the last one
  b:
    input-processor: ai
    contextual-prompt: bye"""


def test_an_unformatted_file_comes_out_formatted_and_a_formatted_one_unchanged():
    assert IndexYmlFormatter().format(UNFORMATTED) == FORMATTED
    assert IndexYmlFormatter().format(FORMATTED) == FORMATTED


def test_a_trigger_already_wrapped_in_parentheses_is_not_wrapped_again():
    text = FORMATTED.replace(
        "          (env.x > 0 and\n          (env.y < 3 or\n          signal.mood > 50))\n",
        "          (env.x > 0 and\n           (env.y < 3 or signal.mood > 50))\n",
    )

    assert IndexYmlFormatter().format(text) == FORMATTED


def test_a_single_condition_trigger_keeps_its_own_spelling():
    text = FORMATTED.replace('trigger: "True"', "trigger: signal.mood   >   50")

    assert IndexYmlFormatter().format(text) == text


def test_a_file_that_is_not_yaml_is_returned_as_written():
    text = "states: [a\n"

    assert IndexYmlFormatter().format(text) == text


def test_a_file_whose_meaning_the_layout_would_change_is_returned_as_written():
    text = "project:\n  id: kept\n  ui-description: |+\n    trailing\n\n\n\ninit-action:\n  target: a\n"

    assert IndexYmlFormatter().format(text) == text
