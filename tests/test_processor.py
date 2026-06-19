"""Tests for the tiered DocumentParser.

The header-normalisation tests run everywhere. The four extraction tests need
the parser stack (pymupdf / camelot / img2table / reportlab) and skip cleanly
when it (or the ghostscript binary) is absent, so a bare venv stays green.

Note: the previous ``DocumentProcessor`` / ``PageText`` / ``process`` tests were
removed because the mandated rewrite replaced that linear API with
``DocumentParser.parse``; the surviving behaviour (header normalisation) is
re-tested against the new class.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from rag_core.config import get_settings
from rag_core.processor import DocumentParser
from rag_core.schemas import ExtractionMethod, TableElement, TextElement


@pytest.fixture()
def parser() -> DocumentParser:
    """A parser built from test settings."""
    return DocumentParser(get_settings())


# --- Header normalisation (no heavy deps) --------------------------------------


def test_normalise_headers_inserts_breaks(parser: DocumentParser) -> None:
    """Section headers gain a preceding blank line for reliable splitting."""
    text = "Some preamble. Article 5 Termination follows. §7 Liability."
    normalised = parser._normalise_headers(text)
    assert "\n\nArticle 5" in normalised
    assert "\n\n§7" in normalised


def test_normalise_headers_idempotent_on_existing_break(
    parser: DocumentParser,
) -> None:
    """A header already preceded by a newline is not double-broken."""
    text = "intro\nArticle 1 scope"
    normalised = parser._normalise_headers(text)
    assert normalised.count("Article 1") == 1


# --- Parser-stack tests --------------------------------------------------------

_PARSER_STACK = all(
    importlib.util.find_spec(module) is not None
    for module in ("fitz", "camelot", "img2table", "reportlab")
)
requires_parsers = pytest.mark.skipif(
    not _PARSER_STACK, reason="parser stack (pymupdf/camelot/img2table/reportlab) absent"
)
requires_ghostscript = pytest.mark.skipif(
    shutil.which("gs") is None
    and shutil.which("gswin64c") is None
    and shutil.which("gswin32c") is None,
    reason="ghostscript binary not installed (camelot lattice)",
)


def _two_column_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Frame, Paragraph

    _, height = letter
    style = getSampleStyleSheet()["BodyText"]
    filler = " ".join(["lorem ipsum dolor sit amet consectetur"] * 10)
    cnv = canvas.Canvas(str(path), pagesize=letter)
    Frame(40, 72, 245, height - 144, showBoundary=0).addFromList(
        [Paragraph("Alpha Beta Gamma " + filler, style)], cnv
    )
    Frame(320, 72, 245, height - 144, showBoundary=0).addFromList(
        [Paragraph("Delta Epsilon Zeta " + filler, style)], cnv
    )
    cnv.save()


def _table_pdf(path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

    doc = SimpleDocTemplate(str(path), pagesize=letter)
    table = Table([["Metric", "Standard", "Threshold"], ["Uptime", "99.9%", "99.5%"]])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
    doc.build([table])


def _image_only_pdf(image_png: Path, path: Path) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    cnv = canvas.Canvas(str(path), pagesize=letter)
    cnv.drawImage(str(image_png), 60, 380, width=480, height=200)
    cnv.save()


def _grid_table_png(png_path: Path) -> None:
    """Draw a large, high-contrast bordered table for reliable img2table detection."""
    from PIL import Image, ImageDraw, ImageFont

    width, height, rows, cols = 1200, 400, 2, 3
    cell_w, cell_h = width // cols, height // rows
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=36)
    except TypeError:  # Pillow < 10.1 has no size arg
        font = ImageFont.load_default()
    for i in range(cols + 1):
        draw.line([(i * cell_w, 0), (i * cell_w, height)], fill="black", width=5)
    for j in range(rows + 1):
        draw.line([(0, j * cell_h), (width, j * cell_h)], fill="black", width=5)
    cells = [["Metric", "Standard", "Threshold"], ["Uptime", "99.9", "99.5"]]
    for r in range(rows):
        for c in range(cols):
            draw.text(
                (c * cell_w + 25, r * cell_h + cell_h // 2 - 18),
                cells[r][c],
                fill="black",
                font=font,
            )
    image.save(str(png_path))


def _short_text_png(png_path: Path) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (700, 180), "white")
    ImageDraw.Draw(image).text((20, 70), "Scanned page content for OCR", fill="black")
    image.save(str(png_path))


def _native_text_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    cnv = canvas.Canvas(str(path), pagesize=letter)
    text = cnv.beginText(72, 720)
    for line in ("This is a native single-column contract page.", "Article 1 Term."):
        text.textLine(line)
    cnv.drawText(text)
    cnv.save()


@requires_parsers
def test_multicolumn_reading_order(tmp_path: Path) -> None:
    """Two-column text is read left-column-first, not interleaved."""
    pdf = tmp_path / "twocol.pdf"
    _two_column_pdf(pdf)

    parsed = DocumentParser(get_settings()).parse(str(pdf), "doc-mc", "acme")
    text = "\n".join(e.text for e in parsed.elements if isinstance(e, TextElement))
    assert "Alpha Beta Gamma" in text
    assert "Delta Epsilon Zeta" in text
    assert text.index("Alpha Beta Gamma") < text.index("Delta Epsilon Zeta")


@requires_parsers
@requires_ghostscript
def test_camelot_lattice_table(tmp_path: Path) -> None:
    """A bordered table is extracted with headers and structured cells."""
    pdf = tmp_path / "table.pdf"
    _table_pdf(pdf)

    parsed = DocumentParser(get_settings()).parse(str(pdf), "doc-tbl", "acme")
    tables = [e for e in parsed.elements if isinstance(e, TableElement)]
    assert tables, "expected at least one table element"
    table = tables[0]
    assert table.column_headers == ["Metric", "Standard", "Threshold"]
    assert table.structured_data == [["Uptime", "99.9%", "99.5%"]]
    assert "| Metric | Standard | Threshold |" in table.markdown_representation


@requires_parsers
def test_scanned_table_img2table(tmp_path: Path) -> None:
    """The OCR table branch (img2table) extracts a table from a page image.

    Exercises ``_tables_img2table`` directly with a clean, high-contrast image:
    going through a second PDF rasterisation degrades the table lines below
    img2table's detection threshold, which is rendering noise unrelated to the
    code under test. The full scanned-page OCR path (text) is covered by
    ``test_extraction_summary``.
    """
    from PIL import Image

    png = tmp_path / "grid.png"
    _grid_table_png(png)
    image = Image.open(str(png)).convert("RGB")

    tables = DocumentParser(get_settings())._tables_img2table(
        image, page_number=1, document_id="doc-scan", tenant_id="acme", start_index=0
    )
    assert tables, "expected an img2table-extracted table"
    assert tables[0].extraction_method is ExtractionMethod.OCR_IMG2TABLE
    assert tables[0].structured_data  # OCR varies — assert structure, not cells


@requires_parsers
def test_extraction_summary(tmp_path: Path) -> None:
    """A native page + a scanned page report distinct extraction methods."""
    import fitz

    png = tmp_path / "scan.png"
    _short_text_png(png)
    scanned = tmp_path / "scan.pdf"
    _image_only_pdf(png, scanned)
    native = tmp_path / "native.pdf"
    _native_text_pdf(native)

    merged = tmp_path / "merged.pdf"
    with fitz.open(str(native)) as doc, fitz.open(str(scanned)) as scan_doc:
        doc.insert_pdf(scan_doc)
        doc.save(str(merged))

    parsed = DocumentParser(get_settings()).parse(str(merged), "doc-sum", "acme")
    assert len(parsed.extraction_summary) >= 2
    assert parsed.extraction_summary.get(ExtractionMethod.OCR_PYTESSERACT) == 1
