from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

BIN = Path(__file__).resolve().parents[1] / "bin" / "strip_comments.py"

_spec = importlib.util.spec_from_file_location("strip_comments", BIN)
strip_comments = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(strip_comments)


def strip(name, text, *families):
    stripper = strip_comments.Stripper(strip_comments.Selection(families))
    return stripper.stripped(Path(name), text)


def test_a_python_comment_goes_and_the_code_on_its_line_stays():
    assert strip("a.py", "x = 1  # gone\n# gone too\ny = 2\n") == "x = 1\ny = 2\n"


def test_xxx_and_fixme_comments_survive():
    source = "# XXX open question\nx = 1  # FIXME later\n#### XXX under decoration\n"
    assert strip("a.py", source) == source


def test_a_shebang_a_coding_cookie_and_a_tool_directive_are_not_comments():
    source = "#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\nfrom x import y  # noqa: F401\n"
    assert strip("a.py", source) == source


def test_a_hash_inside_a_python_string_is_left_alone():
    assert strip("a.py", 'x = "# not a comment"  # gone\n') == 'x = "# not a comment"\n'


def test_slashes_inside_javascript_strings_templates_and_regexes_are_left_alone():
    source = (
        'const url = "https://example.com/x"; // gone\n'
        "const t = `a // not a comment`;\n"
        "const re = /https:\\/\\/[^/]+\\//g; // gone\n"
        "const cls = /[/]/;\n"
    )
    expected = (
        'const url = "https://example.com/x";\n'
        "const t = `a // not a comment`;\n"
        "const re = /https:\\/\\/[^/]+\\//g;\n"
        "const cls = /[/]/;\n"
    )
    assert strip("a.js", source) == expected


def test_a_javascript_block_comment_goes_and_its_line_survives():
    assert strip("a.js", "if (x) { /* gone */ y(); }\n") == "if (x) {  y(); }\n"


def test_a_multi_line_jsdoc_block_goes_whole():
    assert strip("a.js", "/**\n * gone\n */\nfunction f() {}\n") == "function f() {}\n"


def test_an_eslint_directive_is_not_a_comment():
    source = "/* eslint-disable no-console */\nconsole.log(1)\n"
    assert strip("a.js", source) == source


def test_only_the_script_block_of_a_vue_file_is_stripped_by_default():
    source = (
        "<script setup>\n// gone\nconst a = 1 // XXX kept\n</script>\n"
        "\n<template>\n  <!-- kept -->\n  <p>a // b and /* c */ stay</p>\n</template>\n"
        "\n<style scoped>\n/* kept */\n.a { color: red }\n</style>\n"
    )
    expected = (
        "<script setup>\nconst a = 1 // XXX kept\n</script>\n"
        "\n<template>\n  <!-- kept -->\n  <p>a // b and /* c */ stay</p>\n</template>\n"
        "\n<style scoped>\n/* kept */\n.a { color: red }\n</style>\n"
    )
    assert strip("a.vue", source) == expected


def test_styles_and_markup_reach_the_other_blocks_of_a_vue_file():
    source = (
        "<script setup>\nconst a = 1\n</script>\n"
        "\n<template>\n  <!-- gone -->\n  <p>a // b and /* c */ stay</p>\n  <!-- XXX kept -->\n</template>\n"
        "\n<style scoped>\n/* gone */\n.b::after { content: \"/* not a comment */\"; }\n</style>\n"
    )
    expected = (
        "<script setup>\nconst a = 1\n</script>\n"
        "\n<template>\n  <p>a // b and /* c */ stay</p>\n  <!-- XXX kept -->\n</template>\n"
        "\n<style scoped>\n.b::after { content: \"/* not a comment */\"; }\n</style>\n"
    )
    assert strip("a.vue", source, strip_comments.STYLES, strip_comments.MARKUP) == expected


def test_a_stylesheet_is_untouched_unless_styles_is_asked_for():
    stripper = strip_comments.Stripper()
    assert stripper.handles(Path("a.css")) is False
    widened = strip_comments.Stripper(strip_comments.Selection([strip_comments.STYLES]))
    assert widened.handles(Path("a.css")) is True
    assert strip("a.css", ".a { color: red }\n/* gone */\n", strip_comments.STYLES) == ".a { color: red }\n"


def test_markup_is_untouched_unless_markup_is_asked_for():
    stripper = strip_comments.Stripper()
    assert stripper.handles(Path("a.html")) is False
    source = "<p>hi</p>\n<!-- gone -->\n"
    assert strip("a.html", source, strip_comments.MARKUP) == "<p>hi</p>\n"


def test_a_file_the_tool_does_not_know_is_never_touched():
    assert strip_comments.Stripper().handles(Path("a.md")) is False
