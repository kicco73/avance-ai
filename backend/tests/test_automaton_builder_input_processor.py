"""Every state says who answers in it. `ai` is the model, and needs a
prompt. `system` is the automaton alone: no model reads its prompt, no
model computes signals for its scripts, and the reply is what an action
reaching it writes with chat.write — which is why chat.write exists only
on the way into a system state."""
from __future__ import annotations

import pytest

from automaton.automaton_builder import AutomatonBuilder
from automaton.build_error import AutomatonBuildError

pytestmark = pytest.mark.contract

BASE = """\
project:
  id: processors
init-action:
  target: a
  on-exit: |
    {init_on_exit}
signals:
  mood:
    definition: How they feel.
env:
  step:
    type: number
states:
  a:
    input-processor: ai
    contextual-prompt: hi
    actions:
      - name: go
        target: b
        on-exit: |
          {a_on_exit}
  b:
{b}
    actions:
      - name: next
        target: b
        trigger: "{b_trigger}"
        on-exit: |
          {b_on_exit}
        task: |
          {b_task}
"""

SYSTEM_B = "    input-processor: system\n    contextual-prompt: ignored"


def _build(**parts):
    filled = {
        "init_on_exit": "env.step = 0", "a_on_exit": "env.step = 1", "b": SYSTEM_B, "b_trigger": "env.step > 0",
        "b_on_exit": "env.step = env.step", "b_task": "task.send_mail(user.email, 'x')",
        **parts,
    }
    return AutomatonBuilder().build({"index.yml": BASE.format(**filled)})


def _refused(**parts) -> str:
    with pytest.raises(AutomatonBuildError) as caught:
        _build(**parts)
    return str(caught.value)


def test_a_state_declares_its_processor_or_does_not_build():
    message = _refused(b="    contextual-prompt: hi")

    assert "State 'b': 'input-processor' is required" in message
    assert "'ai'" in message and "'system'" in message


def test_an_unknown_processor_is_refused_with_the_two_that_exist():
    message = _refused(b="    input-processor: human\n    contextual-prompt: hi")

    assert "'input-processor' is required" in message and "got 'human'" in message


def test_a_system_state_ignores_its_prompt_and_never_takes_chat():
    automaton = _build(b=SYSTEM_B + "\n    chat-enabled: true")

    state = automaton.get_state("b")
    assert state.input_processor == "system"
    assert state.chat_enabled is False
    assert automaton.get_state("a").input_processor == "ai"
    assert automaton.get_state("a").chat_enabled is True


def test_a_system_state_s_input_and_output_are_not_checked_against_the_model():
    automaton = _build(b=SYSTEM_B + "\n    input:\n      - step\n    output:\n      - step")

    assert automaton.get_state("b").input == ("step",)


def test_an_ai_state_still_needs_its_prompt():
    assert "State 'b': 'contextual-prompt' is required for input-processor: ai" in _refused(
        b="    input-processor: ai",
    )


def test_fixed_message_is_gone_and_says_what_replaced_it():
    message = _refused(b="    input-processor: ai\n    fixed-message: hello")

    assert "'fixed-message' is no longer a field" in message
    assert "input-processor: system" in message and "chat.write" in message


@pytest.mark.parametrize("slot,expression", [
    ("b_trigger", "signal.mood > 50"),
    ("b_on_exit", "env.step = signal.mood"),
    ("b_task", "task.send_mail(user.email, str(signal.mood))"),
])
def test_no_script_of_a_system_state_can_read_a_signal(slot, expression):
    assert "references undefined name(s): signal.mood" in _refused(**{slot: expression})


def test_an_ai_state_reads_signals_in_every_script():
    assert _build(b="    input-processor: ai\n    contextual-prompt: hi", b_trigger="signal.mood > 50") is not None


def test_chat_write_reaches_a_system_state():
    automaton = _build(a_on_exit="chat.write('Step 1')", b_on_exit="chat.write('Step %d' % env.step)")

    assert automaton.get_state("a").actions[0].on_exit.strip() == "chat.write('Step 1')"


def test_chat_write_table_reaches_a_system_state():
    automaton = _build(a_on_exit="chat.write_table({'n': ['x', 1, True]})")

    assert automaton.get_state("a").actions[0].on_exit.strip() == "chat.write_table({'n': ['x', 1, True]})"


@pytest.mark.parametrize("slot", ["init_on_exit", "b_on_exit"])
@pytest.mark.parametrize("call", ["chat.write('nobody reads this')", "chat.write_table({'h': ['nobody reads this']})"])
def test_chat_write_has_no_reader_on_the_way_into_an_ai_state_a_self_loop_included(slot, call):
    ai_b = "    input-processor: ai\n    contextual-prompt: hi"
    message = _refused(b=ai_b, **{slot: call})

    assert f"references undefined name(s): {call.split('(')[0]}" in message


def test_chat_write_takes_exactly_one_argument():
    assert "chat.write(...)" in _refused(a_on_exit="chat.write()")


def test_chat_write_table_takes_exactly_one_table():
    assert "chat.write_table(...)" in _refused(a_on_exit="chat.write_table({'h': ['x']}, {})")


def test_the_build_time_kinds_and_the_runtime_processors_are_the_same_two():
    """`ai` is not a runtime processor core owns — it's contributed by
    the ai skill onto POINT_INPUT_PROCESSORS (see turn/input_processor.py),
    seeded back in for tests by conftest.py's own _reset_bus. The
    build-time kind still knows about it unconditionally: validating
    `input-processor: ai` in a project's YAML doesn't need the skill
    installed, the same way task.send_mail's identifier is always valid
    grammar whether or not mail is."""
    from automaton.input_processor_kind import INPUT_PROCESSOR_KINDS
    from turn.input_processor import processors

    assert processors().keys() == INPUT_PROCESSOR_KINDS.keys() == {"ai", "system"}
