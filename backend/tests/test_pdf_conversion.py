"""tracking.actuators.pdf_conversion.convert_to_pdf: the heuristic that
sorts `drive.save_as_pdf`'s raw `content` into one of the four shapes it
accepts (image, pdf, csv, text) before converting it, and the conversion
itself. Observes only `convert_to_pdf`'s own return value."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from tracking.actuators.pdf_conversion import convert_to_pdf


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


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
