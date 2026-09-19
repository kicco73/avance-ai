"""AutomatonValidator.validate_expression_types — the one static type gate
every expression a project can run goes through: an action's `trigger`,
its `env:` writes, its `on-exit` assignments and its `task` lines. Same
two checks everywhere
(TriggerExpressionAnalyzer.type_violations + signal_domain_violations), so
a mistake is caught wherever it is written, not only in a trigger.
"""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder

pytestmark = pytest.mark.contract

MISTYPED = "signal.mood == 'alto'"
OUT_OF_RANGE = "signal.mood > 150"
ORDERED_STRING = "user.name >= 5"


def _project(env_yaml: str = "", action_yaml: str = "") -> str:
    return f"""
project:
  id: test_project
signals:
  mood:
    definition: how happy the user seems
env:
  stage:
    type: string
{env_yaml}init-action:
  target: a
states:
  a:
    contextual-prompt: hi
    actions:
      - name: go
        target: b
{action_yaml}  b:
    contextual-prompt: there
"""


def _build(env_yaml: str = "", action_yaml: str = ""):
    return AutomatonBuilder().build({"index.yml": _project(env_yaml, action_yaml)})


@pytest.mark.parametrize("expression", [MISTYPED, OUT_OF_RANGE, ORDERED_STRING])
@pytest.mark.parametrize(("env_yaml", "action_yaml", "context"), [
    ("", "        trigger: \"{expression}\"\n", "trigger"),
    ("", "        env:\n          stage: \"'x' if {expression} else 'y'\"\n", "env expression for 'stage'"),
    ("", "        on-exit: |\n          env.stage = 'x' if {expression} else 'y'\n", "on-exit line 1"),
    ("", "        task: |\n          task.send_mail('a', 'b' if {expression} else 'c')\n", "task line 1"),
], ids=["trigger", "action-env", "on-exit", "task"])
def test_the_same_mistake_is_rejected_at_build_time_wherever_the_expression_is_written(
    expression, env_yaml, action_yaml, context
):
    with pytest.raises(ValueError) as error:
        _build(env_yaml.format(expression=expression), action_yaml.format(expression=expression))

    assert context in str(error.value)


@pytest.mark.parametrize(("env_yaml", "action_yaml"), [
    ("", "        trigger: \"signal.mood >= 75\"\n"),
    ("", "        env:\n          stage: \"'x' if signal.mood >= 75 else 'y'\"\n"),
    ("", "        on-exit: |\n          env.stage = 'x' if signal.mood >= 75 else 'y'\n"),
    ("", "        task: |\n          task.send_mail('a', 'b' if signal.mood >= 75 else 'c')\n"),
], ids=["trigger", "action-env", "on-exit", "task"])
def test_an_expression_the_gate_has_nothing_against_still_builds(env_yaml, action_yaml):
    assert _build(env_yaml, action_yaml).states["a"].actions[0].name == "go"
