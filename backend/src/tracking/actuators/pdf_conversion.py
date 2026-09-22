from __future__ import annotations

import csv
import io

import markdown
from fpdf import FPDF
from PIL import Image, UnidentifiedImageError

_PDF_MAGIC = b"%PDF-"
_CSV_DELIMITERS = (",", ";", "\t")
_PAGE_MARGIN_PT = 15


def convert_to_pdf(content: str | bytes) -> bytes:
    """`content` — the same shape `drive.write` accepts, no filename or
    content-type attached to it — heuristically sniffed as one of the
    four shapes `drive.save_as_pdf` accepts (image, pdf, csv, text) and
    turned into a PDF. Anything else raises."""
    data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    if data.startswith(_PDF_MAGIC):
        return data
    if _looks_like_image(data):
        return _image_to_pdf(data)
    text = content if isinstance(content, str) else _decoded_or_none(data)
    if text is None:
        raise ValueError(
            "drive.save_as_pdf: content is none of an image, a PDF, CSV or text — nothing this can convert."
        )
    if _looks_like_csv(text):
        return _csv_to_pdf(text)
    return _text_to_pdf(text)


def _decoded_or_none(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _looks_like_image(data: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        return True
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return False


def _looks_like_csv(text: str) -> bool:
    """A real table, not prose that happens to contain commas: every
    non-blank line must split into the same number of fields (2+) under
    one delimiter. A single ambiguous line, or a line count that varies,
    is text — csv.Sniffer alone false-positives on plain sentences."""
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    for delimiter in _CSV_DELIMITERS:
        try:
            rows = list(csv.reader(lines, delimiter=delimiter))
        except csv.Error:
            continue
        field_counts = {len(row) for row in rows}
        if len(field_counts) == 1 and next(iter(field_counts)) >= 2:
            return True
    return False


def _image_to_pdf(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        image = image.convert("RGB")
        pdf = FPDF(unit="pt", format=(image.width, image.height))
        pdf.add_page()
        pdf.image(image, x=0, y=0, w=image.width, h=image.height)
    return bytes(pdf.output())


def _csv_to_pdf(text: str) -> bytes:
    rows = list(csv.reader(text.splitlines()))
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=_PAGE_MARGIN_PT)
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    with pdf.table() as table:
        for row in rows:
            table.row(row)
    return bytes(pdf.output())


def _text_to_pdf(text: str) -> bytes:
    """`text` is typically a task.prompt(...) result: markdown, not
    plain prose (see drive.write's own example). Rendered as markdown
    either way — plain text has no markdown syntax to render, so it
    comes out unchanged."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=_PAGE_MARGIN_PT)
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.write_html(markdown.markdown(text))
    return bytes(pdf.output())
