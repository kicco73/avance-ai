"""tracking.actuators.pdf_conversion.convert_to_pdf: the heuristic that
sorts `drive.save_as_pdf`'s raw `content` into one of the four shapes it
accepts (image, pdf, csv, text) before converting it, and the conversion
itself. Observes only `convert_to_pdf`'s own return value."""
from __future__ import annotations

import io

import pytest
from PIL import Image
from pypdf import PdfReader

from tracking.actuators.pdf_conversion import convert_to_pdf


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _extracted_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() for page in reader.pages)


def test_a_pdf_is_returned_unchanged():
    pdf_bytes = b"%PDF-1.4\nwhatever a real PDF has in here\n%%EOF"

    assert convert_to_pdf(pdf_bytes) == pdf_bytes


def test_markdown_text_becomes_a_pdf():
    out = convert_to_pdf("# Title\n\nSome **bold** text.")

    assert out.startswith(b"%PDF-")


def test_plain_text_with_commas_is_not_mistaken_for_csv_and_still_becomes_a_pdf():
    out = convert_to_pdf("Ciao Marco, come stai?\nBene, grazie, e tu? Tutto bene, spero.")

    assert out.startswith(b"%PDF-")


def test_a_csv_table_becomes_a_pdf():
    out = convert_to_pdf("name,age\nAlice,30\nBob,25\n")

    assert out.startswith(b"%PDF-")


def test_a_semicolon_csv_table_becomes_a_pdf():
    out = convert_to_pdf("nome;eta\nAlice;30\nBob;25\n")

    assert out.startswith(b"%PDF-")


def test_an_image_becomes_a_pdf():
    out = convert_to_pdf(_png_bytes())

    assert out.startswith(b"%PDF-")


def test_bytes_that_are_no_known_shape_are_refused():
    with pytest.raises(ValueError, match="none of an image, a PDF, CSV or text"):
        convert_to_pdf(b"\xff\xfe\x00\x01not any of the four accepted shapes\xff")


def test_a_markdown_table_renders_as_a_real_table_not_the_literal_pipe_syntax():
    out = convert_to_pdf("| Name | Score |\n| --- | --- |\n| Alice | 9 |\n| Bob | 7 |\n")

    text = _extracted_text(out)
    assert "Alice" in text and "9" in text
    assert "|" not in text
    assert "---" not in text


def test_a_fenced_code_block_renders_its_content_not_the_backticks():
    out = convert_to_pdf("```\nprint('hi')\n```\n")

    text = _extracted_text(out)
    assert "print('hi')" in text
    assert "```" not in text


def test_a_heading_and_body_text_both_come_through():
    out = convert_to_pdf("# Report\n\nSome **bold** body text.")

    text = _extracted_text(out)
    assert "Report" in text
    assert "Some bold body text." in text


def test_body_and_table_text_use_the_same_font_family_as_headings():
    """fpdf2's write_html() falls back to Times whenever the FPDF
    instance it's called on has no font of its own set yet — a
    visibly different font from the one headings render in, and from
    what the markdown viewer ever shows."""
    out = convert_to_pdf("# Report\n\nBody text.\n\n| A |\n| --- |\n| 1 |\n")

    reader = PdfReader(io.BytesIO(out))
    base_fonts = [font.get_object()["/BaseFont"] for font in reader.pages[0]["/Resources"]["/Font"].values()]
    assert base_fonts
    assert all("Times" not in name for name in base_fonts)


def test_a_markdown_table_gets_a_real_cell_grid_and_a_shaded_header_row():
    """python-markdown's own table extension emits a bare <table>, and
    fpdf2's write_html() draws that as a single line under the header
    row and nothing else — no box around any cell, no header shading —
    unless the HTML itself carries border/cellpadding/bgcolor, which
    _styled_table_html adds. Checked at the PDF content-stream level
    (drawing operators), since pypdf's text extraction carries no
    layout or fill information to assert on."""
    out = convert_to_pdf("| A | B |\n| --- | --- |\n| 1 | 2 |\n")

    reader = PdfReader(io.BytesIO(out))
    content = reader.pages[0].get_contents().get_data().decode("latin1")
    assert " re B" in content, "expected a filled+bordered rectangle for the shaded header cell"
    assert " re S" in content, "expected a stroked rectangle (a real box) around a data cell"
