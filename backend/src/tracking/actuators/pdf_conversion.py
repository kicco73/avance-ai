from __future__ import annotations

import csv
import io

import markdown
from fpdf import FPDF
from fpdf.fonts import FontFace
from PIL import Image, UnidentifiedImageError

_PDF_MAGIC = b"%PDF-"
_CSV_DELIMITERS = (",", ";", "\t")
_PAGE_MARGIN_PT = 15
_MARKDOWN_EXTENSIONS = ("tables", "fenced_code", "nl2br", "sane_lists")

_BODY_FONT_FAMILY = "helvetica"
_HEADING_COLOR = "#000000"
_BLOCKQUOTE_COLOR = "#666666"
_TABLE_HEADER_FILL = "#f2f2f2"
_TABLE_CELL_PADDING_PT = 5
_HEADING_SIZES_PT = {"h1": 22, "h2": 18, "h3": 15, "h4": 13, "h5": 12, "h6": 11}
_TAG_STYLES = {
    tag: FontFace(family=_BODY_FONT_FAMILY, size_pt=size, emphasis="B", color=_HEADING_COLOR)
    for tag, size in _HEADING_SIZES_PT.items()
}
_TAG_STYLES["blockquote"] = FontFace(color=_BLOCKQUOTE_COLOR)


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
    pdf.set_font(_BODY_FONT_FAMILY, size=10)
    with pdf.table() as table:
        for row in rows:
            table.row(row)
    return bytes(pdf.output())


def _styled_table_html(html: str) -> str:
    """python-markdown's table extension emits bare `<table>`/`<th>`
    with no attributes — fpdf2's write_html only takes its table
    styling (border grid, cell padding, header fill) from HTML
    attributes, not from tag_styles (`<table>`/`<th>`/`<td>` aren't
    styleable tags there), so this is the only lever available short of
    building the PDF table by hand. Safe as a plain substring replace:
    markdown.markdown() never emits these attributes itself, so there is
    nothing here to clash with."""
    html = html.replace("<table>", f'<table border="1" cellpadding="{_TABLE_CELL_PADDING_PT}">')
    return html.replace("<th>", f'<th bgcolor="{_TABLE_HEADER_FILL}">')


def _text_to_pdf(text: str) -> bytes:
    """`text` is typically a task.prompt(...) result: markdown, not
    plain prose (see drive.write's own example). Rendered as markdown
    either way — plain text has no markdown syntax to render, so it
    comes out unchanged. The same extensions webchat's own renderMarkdown
    (markdown-it, tables built in) supports out of the box have to be
    named explicitly here — python-markdown's core is CommonMark only,
    so a table or fenced code block left unnamed doesn't fail, it just
    comes out as the literal source text alongside the parts that did
    render, which reads as broken rather than unsupported."""
    html = _styled_table_html(markdown.markdown(text, extensions=list(_MARKDOWN_EXTENSIONS)))
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=_PAGE_MARGIN_PT)
    pdf.add_page()
    pdf.set_font(_BODY_FONT_FAMILY, size=11)
    pdf.write_html(html, table_line_separators=True, tag_styles=_TAG_STYLES)
    return bytes(pdf.output())
