from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

BIN = Path(__file__).resolve().parents[1] / "bin" / "bump_version.py"

_spec = importlib.util.spec_from_file_location("bump_version", BIN)
bump_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump_version)


def test_the_last_number_grows_and_the_rest_of_the_module_is_untouched():
    source = 'x = "1.0.0"\n\n__version__ = "2.0.9"\n\ny = 1\n'
    assert bump_version.bumped(source) == ('x = "1.0.0"\n\n__version__ = "2.0.10"\n\ny = 1\n', "2.0.10")


def test_an_indented_assignment_is_not_the_module_version():
    with pytest.raises(ValueError):
        bump_version.bumped('class A:\n    __version__ = "2.0.0"\n')


def test_a_module_without_the_line_is_an_error():
    with pytest.raises(ValueError):
        bump_version.bumped("x = 1\n")


def test_the_backend_main_carries_a_line_the_tool_can_bump():
    bump_version.bumped(bump_version.MAIN.read_text(encoding="utf-8"))
