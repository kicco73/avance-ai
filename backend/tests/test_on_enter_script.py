"""OnEnterScriptSignatureParser — build-time validation for an action's
own on-enter field."""
from __future__ import annotations

import pytest

from automaton.on_enter_script import OnEnterScriptError, OnEnterScriptSignatureParser

pytestmark = pytest.mark.contract


@pytest.fixture
def parser() -> OnEnterScriptSignatureParser:
    return OnEnterScriptSignatureParser()


def test_none_and_empty_are_valid(parser):
    parser.validate(None)
    parser.validate("")


def test_blank_and_whitespace_only_lines_are_skipped(parser):
    parser.validate("\n  \ncelebrate()\n\t\n")


def test_a_zero_arg_known_call_is_valid(parser):
    parser.validate("celebrate()")


def test_a_two_arg_known_call_is_valid(parser):
    parser.validate("notify('Nice!', 'You reached **state B**.')")


def test_multiple_calls_one_per_line_is_valid(parser):
    parser.validate("celebrate()\nnotify('a', 'b')")


def test_rejects_a_bare_identifier_with_no_call(parser):
    with pytest.raises(OnEnterScriptError, match="line 1.*expected a single function call"):
        parser.validate("celebrate")


def test_rejects_an_unknown_function_name(parser):
    with pytest.raises(OnEnterScriptError, match="line 1.*unknown function 'doStuff'"):
        parser.validate("doStuff()")


def test_rejects_the_wrong_argument_count(parser):
    with pytest.raises(OnEnterScriptError, match="line 1.*'celebrate\\(\\)' takes 0 arguments, got 1"):
        parser.validate("celebrate(42)")


def test_rejects_too_few_arguments(parser):
    with pytest.raises(OnEnterScriptError, match="'notify\\(\\)' takes 2 arguments, got 1"):
        parser.validate("notify('only one')")


def test_rejects_keyword_arguments(parser):
    with pytest.raises(OnEnterScriptError, match="doesn't accept keyword arguments"):
        parser.validate("notify(title='a', body='b')")


def test_rejects_a_starred_argument(parser):
    with pytest.raises(OnEnterScriptError, match="doesn't accept a starred argument"):
        parser.validate("notify(*args)")


def test_rejects_attribute_access_as_the_call_target(parser):
    with pytest.raises(OnEnterScriptError, match="not an attribute/subscript"):
        parser.validate("console.log('hi')")


def test_rejects_multiple_statements_on_one_line(parser):
    with pytest.raises(OnEnterScriptError, match="line 1"):
        parser.validate("celebrate(); notify('a', 'b')")


def test_rejects_a_syntax_error(parser):
    with pytest.raises(OnEnterScriptError, match="line 1.*not a valid function call"):
        parser.validate("celebrate(")


def test_error_reports_the_correct_line_number_in_a_multi_line_script(parser):
    with pytest.raises(OnEnterScriptError, match="line 2"):
        parser.validate("celebrate()\ndoStuff()")


def test_custom_known_functions_override_the_default_set():
    parser = OnEnterScriptSignatureParser(known_functions={"myFunc": 1})
    parser.validate("myFunc(42)")
    with pytest.raises(OnEnterScriptError, match="unknown function 'celebrate'"):
        parser.validate("celebrate()")
