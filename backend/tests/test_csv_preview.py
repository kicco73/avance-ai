"""Tests for project.csv_preview.render_csv_as_markdown_table."""
from __future__ import annotations

import pytest

from project.csv_preview import render_csv_as_markdown_table

pytestmark = pytest.mark.contract


def test_renders_header_and_rows_as_a_markdown_table():
    result = render_csv_as_markdown_table("city,country\nParis,France\nBerlin,Germany\n")

    assert result == (
        "| city | country |\n"
        "| --- | --- |\n"
        "| Paris | France |\n"
        "| Berlin | Germany |"
    )


def test_empty_content_renders_a_placeholder():
    assert render_csv_as_markdown_table("") == "*(empty)*"


def test_header_only_renders_just_the_header_row():
    result = render_csv_as_markdown_table("city,country\n")
    assert result == "| city | country |\n| --- | --- |"


def test_ragged_rows_are_padded_to_the_widest_row():
    result = render_csv_as_markdown_table("a,b,c\n1,2\n")
    assert result == "| a | b | c |\n| --- | --- | --- |\n| 1 | 2 |  |"


def test_pipe_characters_in_cells_are_escaped():
    result = render_csv_as_markdown_table("note\na|b\n")
    assert "a\\|b" in result


def test_quoted_fields_with_embedded_commas_are_parsed_as_one_cell():
    result = render_csv_as_markdown_table('name,note\n"Doe, John",hi\n')
    assert "| Doe, John | hi |" in result
