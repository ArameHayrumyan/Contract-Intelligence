"""Tiered document parsing: layout-aware text + table extraction.

ARCHITECTURAL DECISION — why not ``unstructured``:
    ``unstructured`` bundles detectron2 / paddleocr models that add several GB to
    the image and need 2-4 GB RAM at runtime, which OOMs the current Droplet
    running alongside FastAPI, Chroma, and the bge-small embedding model. The
    tiered strategy below covers both target failure modes (complex tables,
    multi-column layouts) with a CPU-only, no-model-download toolchain
    (pymupdf + camelot + img2table) — well under ~0.5 GB of image growth, versus
    ``unstructured``'s 3-4 GB. See ``docs/SCALING_PATH.md``: if document types
    ever expand beyond PDF, replace this with ``unstructured`` deployed as an
    isolated *sidecar* container, never inline in the API process.

Per-page decision tree (see :meth:`DocumentParser.parse`):
    1. Classify layout (pymupdf blocks): single-column / multi-column / scanned.
    2. Detect tables (camelot lattice → stream) on native pages.
    3. Extract text per layout (pdfplumber / pymupdf-by-column / OCR + img2table).
    4. Regex pre-pass to normalise section headers.
    5. Chunk text (RecursiveCharacterTextSplitter); tables are never split.

Heavy optional parsers (fitz, camelot, img2table) are imported lazily inside the
methods that use them, so this module still imports where they are absent (e.g.
a bare venv) — the tests skip rather than erroring on import.
"""

from __future__ import annotations

import io
import logging
import re
import uuid
from enum import Enum

import cv2
import numpy as np
import pdfplumber
import pytesseract
from langchain_text_splitters import RecursiveCharacterTextSplitter
from PIL import Image

from rag_core.config import Settings
from rag_core.schemas import (
    ExtractionMethod,
    ParsedDocument,
    TableElement,
    TextElement,
)

logger = logging.getLogger("rag_core.processor")

#: Section-header patterns; a double newline is inserted ahead of each match so
#: the "\n\n" splitter separator respects document hierarchy even when headers
#: run inline with body text (Section 3.3).
_SECTION_HEADER_RE = re.compile(
    r"(?<!\n)(?P<hdr>(?:Article|Clause|Section)\s+\d+|§\s*\d+)",
    flags=re.IGNORECASE,
)

#: Camelot's built-in accuracy metric below which a detected table is discarded.
_TABLE_MIN_ACCURACY = 85.0

#: Fraction of page width two non-overlapping blocks must span to be "columns".
_MULTI_COLUMN_WIDTH_RATIO = 0.70

#: A page whose mean chars/block falls below this is treated as scanned.
_MIN_AVG_BLOCK_CHARS = 10


class _PageLayout(Enum):
    """Per-page layout classification driving the extraction path."""

    SINGLE_COLUMN_NATIVE = "single_column_native"
    MULTI_COLUMN = "multi_column"
    SCANNED = "scanned"


class DocumentParser:
    """Parses PDFs into ordered text/table elements with provenance.

    OCR uses :mod:`pytesseract` (small image, no heavy ML runtime); ``easyocr``
    is intentionally not used. Table extraction is camelot (native) / img2table
    (scanned); multi-column reconstruction is pymupdf block ordering.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialise the parser and its text splitter.

        Args:
            settings: Application settings (OCR threshold, etc.).
        """
        self._settings = settings
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=1200,
            chunk_overlap=250,
            separators=["\n\n", "\n", " ", ""],
        )

    # --- Public API ----------------------------------------------------------

    def parse(
        self, pdf_path: str, document_id: str, tenant_id: str
    ) -> ParsedDocument:
        """Parse a PDF into a :class:`ParsedDocument`.

        Args:
            pdf_path: Filesystem path to the (already validated) PDF.
            document_id: Owning document id.
            tenant_id: Owning tenant id (Constraint #2 — never omitted).

        Returns:
            The parsed document: ordered elements + per-method extraction counts.

        Raises:
            ValueError: If the PDF yields no extractable content at all.
        """
        import fitz  # lazy: pymupdf

        elements: list[TableElement | TextElement] = []
        summary: dict[ExtractionMethod, int] = {}

        with pdfplumber.open(pdf_path) as pl_pdf, fitz.open(pdf_path) as fitz_doc:
            total_pages = fitz_doc.page_count
            for index in range(total_pages):
                page_number = index + 1
                fitz_page = fitz_doc[index]
                layout = self._classify_page(fitz_page)

                page_elements: list[TableElement | TextElement] = []

                # STEP 2 — tables on native pages.
                if layout is not _PageLayout.SCANNED:
                    page_elements.extend(
                        self._tables_camelot(
                            pdf_path, page_number, document_id, tenant_id
                        )
                    )

                # STEP 3 — text per layout (+ img2table on scanned pages).
                if layout is _PageLayout.SCANNED:
                    image = self._render_page(fitz_page)
                    raw_text = self._ocr_image(image)
                    text_method = ExtractionMethod.OCR_PYTESSERACT
                    page_elements.extend(
                        self._tables_img2table(
                            image,
                            page_number,
                            document_id,
                            tenant_id,
                            start_index=len(page_elements),
                        )
                    )
                elif layout is _PageLayout.MULTI_COLUMN:
                    raw_text = self._extract_multicolumn(fitz_page)
                    text_method = ExtractionMethod.PYMUPDF_MULTICOLUMN
                else:
                    raw_text = pl_pdf.pages[index].extract_text() or ""
                    text_method = ExtractionMethod.PDFPLUMBER_NATIVE

                page_elements.extend(
                    self._chunk_text(
                        raw_text, text_method, page_number, document_id, tenant_id
                    )
                )

                for element in page_elements:
                    summary[element.extraction_method] = (
                        summary.get(element.extraction_method, 0) + 1
                    )
                elements.extend(page_elements)

        if not elements:
            raise ValueError(
                "No extractable content produced (document may be image-only and "
                "OCR returned nothing)."
            )

        logger.info(
            "Parsed document=%s tenant=%s pages=%d elements=%d methods=%s",
            document_id,
            tenant_id,
            total_pages,
            len(elements),
            {m.value: n for m, n in summary.items()},
        )
        return ParsedDocument(
            document_id=document_id,
            tenant_id=tenant_id,
            total_pages=total_pages,
            elements=elements,
            extraction_summary=summary,
        )

    # --- STEP 1: layout classification ---------------------------------------

    def _classify_page(self, page: object) -> _PageLayout:
        """Classify a page as single-column, multi-column, or scanned.

        Args:
            page: A ``fitz`` page.

        Returns:
            The page's :class:`_PageLayout`.
        """
        blocks = self._text_blocks(page)
        if not blocks:
            return _PageLayout.SCANNED
        avg_chars = sum(len(b[4].strip()) for b in blocks) / len(blocks)
        if avg_chars < _MIN_AVG_BLOCK_CHARS:
            return _PageLayout.SCANNED

        width = float(page.rect.width) or 1.0  # type: ignore[attr-defined]
        for first in blocks:
            for second in blocks:
                if first is second:
                    continue
                # first entirely left of second (x-ranges do not overlap)
                if first[2] <= second[0]:
                    combined = (first[2] - first[0]) + (second[2] - second[0])
                    if combined / width > _MULTI_COLUMN_WIDTH_RATIO:
                        return _PageLayout.MULTI_COLUMN
        return _PageLayout.SINGLE_COLUMN_NATIVE

    @staticmethod
    def _text_blocks(page: object) -> list[tuple]:  # type: ignore[type-arg]
        """Return non-empty text blocks ``(x0, y0, x1, y1, text, no, type)``.

        Args:
            page: A ``fitz`` page.

        Returns:
            Text-type blocks with non-blank content.
        """
        blocks = page.get_text("blocks")  # type: ignore[attr-defined]
        return [b for b in blocks if len(b) >= 7 and b[6] == 0 and b[4].strip()]

    # --- STEP 2: native table extraction (camelot) ---------------------------

    def _tables_camelot(
        self, pdf_path: str, page_number: int, document_id: str, tenant_id: str
    ) -> list[TableElement]:
        """Extract native tables: lattice first, then stream fallback.

        Args:
            pdf_path: Path to the PDF.
            page_number: 1-based page to scan.
            document_id: Owning document id.
            tenant_id: Owning tenant id.

        Returns:
            One :class:`TableElement` per detected table (accuracy-filtered).
        """
        # Import from the defining submodule: camelot's __init__ re-exports
        # read_pdf without declaring it, which trips mypy's no-implicit-reexport.
        from camelot.io import read_pdf  # lazy

        detected, method = [], ExtractionMethod.CAMELOT_LATTICE
        for flavor, flavor_method, extra in (
            ("lattice", ExtractionMethod.CAMELOT_LATTICE, {}),
            ("stream", ExtractionMethod.CAMELOT_STREAM, {"edge_tol": 50}),
        ):
            try:
                tables = read_pdf(
                    pdf_path, pages=str(page_number), flavor=flavor, **extra
                )
            except Exception:  # noqa: BLE001 - camelot raises on odd pages
                logger.warning("camelot %s failed on page %d", flavor, page_number)
                continue
            detected = [
                t
                for t in tables
                if t.parsing_report.get("accuracy", 0) >= _TABLE_MIN_ACCURACY
            ]
            method = flavor_method
            if detected:
                break  # lattice found tables; do not also run stream

        elements: list[TableElement] = []
        for table_index, table in enumerate(detected):
            element = self._table_element(
                self._df_to_rows(table.df),
                page_number,
                document_id,
                tenant_id,
                table_index,
                method,
            )
            if element is not None:
                elements.append(element)
        return elements

    # --- STEP 3: text extraction ---------------------------------------------

    def _extract_multicolumn(self, page: object) -> str:
        """Reconstruct reading order on a two-column page via pymupdf blocks.

        pdfplumber concatenates horizontally and garbles two-column legal text;
        here blocks are grouped into left/right columns (split at the page
        midpoint), ordered top-to-bottom within each, then joined left-then-right.

        Args:
            page: A ``fitz`` page.

        Returns:
            Text in human reading order.
        """
        blocks = self._text_blocks(page)
        midpoint = float(page.rect.width) / 2.0  # type: ignore[attr-defined]
        left = sorted((b for b in blocks if b[0] < midpoint), key=lambda b: b[1])
        right = sorted((b for b in blocks if b[0] >= midpoint), key=lambda b: b[1])
        parts = [b[4].strip() for b in left] + [b[4].strip() for b in right]
        return "\n".join(p for p in parts if p)

    def _render_page(self, page: object) -> Image.Image:
        """Render a page to a PIL RGB image for OCR / image table detection.

        Args:
            page: A ``fitz`` page.

        Returns:
            The rendered page image at 300 DPI.
        """
        pixmap = page.get_pixmap(dpi=300, alpha=False)  # type: ignore[attr-defined]
        return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

    def _ocr_image(self, image: Image.Image) -> str:
        """Preprocess (grayscale + Otsu) then OCR a single page image.

        Args:
            image: A PIL RGB image of one page.

        Returns:
            Recognised text (empty string on failure).
        """
        try:
            array = np.array(image)
            gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
            _, binarised = cv2.threshold(
                gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )
            return str(pytesseract.image_to_string(binarised))
        except Exception:  # noqa: BLE001 - OCR is best-effort
            logger.exception("OCR failed for a page image")
            return ""

    def _tables_img2table(
        self,
        image: Image.Image,
        page_number: int,
        document_id: str,
        tenant_id: str,
        *,
        start_index: int,
    ) -> list[TableElement]:
        """Detect tables in a scanned page image via img2table.

        Args:
            image: The rendered page image.
            page_number: 1-based page number.
            document_id: Owning document id.
            tenant_id: Owning tenant id.
            start_index: Index offset for chunk-id numbering on this page.

        Returns:
            One :class:`TableElement` per detected table.
        """
        from img2table.document import Image as Img2TableImage  # lazy
        from img2table.ocr import TesseractOCR  # lazy

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        try:
            doc = Img2TableImage(src=buffer.getvalue())
            extracted = doc.extract_tables(
                ocr=TesseractOCR(lang="eng"),
                implicit_rows=True,
                borderless_tables=True,
            )
        except Exception:  # noqa: BLE001 - best-effort on scans
            logger.warning("img2table failed on page %d", page_number)
            return []

        elements: list[TableElement] = []
        for offset, table in enumerate(extracted):
            element = self._table_element(
                self._df_to_rows(table.df),
                page_number,
                document_id,
                tenant_id,
                start_index + offset,
                ExtractionMethod.OCR_IMG2TABLE,
            )
            if element is not None:
                elements.append(element)
        return elements

    # --- STEP 4 + 5: header normalisation and chunking -----------------------

    def _chunk_text(
        self,
        raw_text: str,
        method: ExtractionMethod,
        page_number: int,
        document_id: str,
        tenant_id: str,
    ) -> list[TextElement]:
        """Normalise headers, split into chunks, and wrap as text elements.

        Args:
            raw_text: Extracted non-table text for the page.
            method: The extraction method that produced ``raw_text``.
            page_number: 1-based page number.
            document_id: Owning document id.
            tenant_id: Owning tenant id.

        Returns:
            One :class:`TextElement` per non-empty chunk.
        """
        normalised = self._normalise_headers(raw_text)
        elements: list[TextElement] = []
        for piece in self._splitter.split_text(normalised):
            piece = piece.strip()
            if not piece:
                continue
            elements.append(
                TextElement(
                    page_number=page_number,
                    chunk_id=str(uuid.uuid4()),
                    extraction_method=method,
                    text=piece,
                    tenant_id=tenant_id,
                )
            )
        return elements

    @staticmethod
    def _normalise_headers(text: str) -> str:
        """Insert a blank line ahead of recognised section headers.

        Args:
            text: Raw page text.

        Returns:
            Text with a double-newline prepended to each detected header.
        """
        return _SECTION_HEADER_RE.sub(r"\n\n\g<hdr>", text)

    # --- Table conversion helpers --------------------------------------------

    def _table_element(
        self,
        rows: list[list[str]],
        page_number: int,
        document_id: str,
        tenant_id: str,
        table_index: int,
        method: ExtractionMethod,
    ) -> TableElement | None:
        """Build a :class:`TableElement` from cleaned cell rows.

        Args:
            rows: Cleaned cells ``[row][col]``.
            page_number: 1-based page number.
            document_id: Owning document id.
            tenant_id: Owning tenant id.
            table_index: 0-based index of the table on the page.
            method: The extraction method that produced the table.

        Returns:
            A populated :class:`TableElement`, or ``None`` if the table is empty.
        """
        rows = [r for r in rows if any(cell for cell in r)]
        if not rows:
            return None
        headers, data = self._split_header(rows)
        markdown = self._build_markdown(headers, data)
        if len(markdown) > 1200:
            logger.warning(
                "Table chunk exceeds 1200 chars (not split): doc=%s page=%d "
                "table=%d chars=%d",
                document_id,
                page_number,
                table_index,
                len(markdown),
            )
        return TableElement(
            page_number=page_number,
            chunk_id=f"{document_id}_table_p{page_number}_{table_index}",
            extraction_method=method,
            markdown_representation=markdown,
            column_headers=headers,
            structured_data=data,
            tenant_id=tenant_id,
        )

    @staticmethod
    def _df_to_rows(frame: object) -> list[list[str]]:
        """Convert a pandas DataFrame to cleaned string cells ``[row][col]``.

        Args:
            frame: A pandas DataFrame from camelot / img2table.

        Returns:
            Cleaned cells with newlines flattened to spaces.
        """
        rows: list[list[str]] = []
        for raw_row in frame.values.tolist():  # type: ignore[attr-defined]
            rows.append(
                [
                    "" if cell is None else str(cell).replace("\n", " ").strip()
                    for cell in raw_row
                ]
            )
        return rows

    @staticmethod
    def _split_header(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
        """Heuristically separate a header row from data rows.

        A first row whose cells are all non-numeric (with at least one data row
        following) is treated as the header; otherwise all rows are data.

        Args:
            rows: Cleaned cell rows.

        Returns:
            ``(column_headers, data_rows)`` — headers empty when none detected.
        """
        if len(rows) > 1 and not any(_is_number(cell) for cell in rows[0]):
            return rows[0], rows[1:]
        return [], rows

    @staticmethod
    def _build_markdown(headers: list[str], data: list[list[str]]) -> str:
        """Render a pipe-delimited markdown table.

        Args:
            headers: Column headers (may be empty).
            data: Data rows.

        Returns:
            Markdown table text (empty string for a zero-column table).
        """
        columns = len(headers) if headers else (len(data[0]) if data else 0)
        if columns == 0:
            return ""
        header_cells = headers if headers else [""] * columns
        lines = [
            "| " + " | ".join(header_cells) + " |",
            "|" + "|".join(["------"] * columns) + "|",
        ]
        for row in data:
            cells = (row + [""] * columns)[:columns]
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)


def _is_number(value: str) -> bool:
    """Return whether a cell parses as a number (ignoring %, $, commas).

    Args:
        value: A table cell.

    Returns:
        ``True`` if the cleaned value is numeric.
    """
    cleaned = value.strip().rstrip("%").lstrip("$").replace(",", "")
    if not cleaned:
        return False
    try:
        float(cleaned)
    except ValueError:
        return False
    return True
