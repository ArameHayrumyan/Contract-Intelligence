"""Document processing: native parse → OCR failover → hierarchy-aware chunking.

Pipeline:
    1. Extract text natively with :mod:`pdfplumber`.
    2. If mean chars/page falls below the configured threshold, the page is
       (likely) a scan — fail over to OCR.
    3. Normalise section headers via a regex pre-pass, then split with
       :class:`RecursiveCharacterTextSplitter`.
    4. Emit :class:`Chunk` objects carrying ``chunk_id`` + ``page_number`` +
       ``tenant_id`` — the provenance that makes auditing possible downstream.
"""

from __future__ import annotations

import io
import logging
import re
import uuid
from dataclasses import dataclass

import cv2
import numpy as np
import pdfplumber
import pytesseract
from langchain_text_splitters import RecursiveCharacterTextSplitter
from PIL import Image

from rag_core.config import Settings
from rag_core.schemas import Chunk

logger = logging.getLogger("rag_core.processor")

#: Section-header patterns. A double newline is inserted *ahead* of each match so
#: the "\n\n" splitter separator reliably respects document hierarchy. Using the
#: literal header strings as separators only works if they appear verbatim; this
#: pre-pass is the robust version of that intent (Section 3.3).
_SECTION_HEADER_RE = re.compile(
    r"(?<!\n)(?P<hdr>(?:Article|Clause|Section)\s+\d+|§\s*\d+)",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class PageText:
    """Text extracted from a single page.

    Attributes:
        page_number: 1-based page index.
        text: Extracted text (native or OCR).
        used_ocr: Whether OCR was used for this page.
    """

    page_number: int
    text: str
    used_ocr: bool


@dataclass(frozen=True)
class ProcessingResult:
    """Outcome of processing one document.

    Attributes:
        chunks: Hierarchy-aware chunks with provenance metadata.
        pages_ocr: Count of pages that required OCR failover.
        page_count: Total pages processed.
    """

    chunks: list[Chunk]
    pages_ocr: int
    page_count: int


class DocumentProcessor:
    """Parses PDFs into provenance-bearing chunks.

    OCR defaults to :mod:`pytesseract` (small image, no heavy ML runtime).
    ``easyocr`` is intentionally *not* the default: it pulls a large Torch
    dependency. Swap it in behind :meth:`_ocr_image` only if a future use case
    needs handwriting / multilingual support — the tradeoff is the image size.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialise the processor and its text splitter.

        Args:
            settings: Application settings (OCR threshold, etc.).
        """
        self._settings = settings
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=1200,
            chunk_overlap=250,
            separators=["\n\n", "\n", " ", ""],
        )

    def process(
        self, *, data: bytes, document_id: str, tenant_id: str
    ) -> ProcessingResult:
        """Process a validated PDF into tenant-scoped chunks.

        Args:
            data: Raw PDF bytes (already passed ``security.validate_upload``).
            document_id: Owning document id.
            tenant_id: Owning tenant id (Constraint #2 — never omitted).

        Returns:
            A :class:`ProcessingResult` with chunks and OCR statistics.

        Raises:
            ValueError: If the PDF yields no extractable text at all.
        """
        pages = self._extract_pages(data)
        pages_ocr = sum(1 for p in pages if p.used_ocr)

        chunks: list[Chunk] = []
        for page in pages:
            normalised = self._normalise_headers(page.text)
            for piece in self._splitter.split_text(normalised):
                piece = piece.strip()
                if not piece:
                    continue
                chunks.append(
                    Chunk(
                        chunk_id=str(uuid.uuid4()),
                        document_id=document_id,
                        tenant_id=tenant_id,
                        page_number=page.page_number,
                        text=piece,
                    )
                )

        if not chunks:
            raise ValueError(
                "No extractable text produced (document may be image-only and "
                "OCR returned nothing)."
            )

        logger.info(
            "Processed document=%s tenant=%s pages=%d ocr_pages=%d chunks=%d",
            document_id,
            tenant_id,
            len(pages),
            pages_ocr,
            len(chunks),
        )
        return ProcessingResult(
            chunks=chunks, pages_ocr=pages_ocr, page_count=len(pages)
        )

    def _extract_pages(self, data: bytes) -> list[PageText]:
        """Extract text per page, failing over to OCR where native text is thin.

        Args:
            data: Raw PDF bytes.

        Returns:
            One :class:`PageText` per page.
        """
        results: list[PageText] = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                native = page.extract_text() or ""
                if self._needs_ocr(native):
                    logger.debug("Page %d below text threshold; using OCR", index)
                    text = self._ocr_page(page)
                    results.append(PageText(index, text, used_ocr=True))
                else:
                    results.append(PageText(index, native, used_ocr=False))
        return results

    def _needs_ocr(self, native_text: str) -> bool:
        """Decide whether a page's native text is too sparse to trust.

        Args:
            native_text: Text extracted natively for one page.

        Returns:
            ``True`` if char count is below the configured per-page threshold.
        """
        return len(native_text.strip()) < self._settings.ocr_char_threshold

    def _ocr_page(self, page: pdfplumber.page.Page) -> str:
        """Render a page to an image and OCR it.

        Args:
            page: A ``pdfplumber`` page.

        Returns:
            OCR-extracted text (empty string if OCR yields nothing).
        """
        try:
            rendered = page.to_image(resolution=300)
            pil_image = rendered.original.convert("RGB")
            return self._ocr_image(pil_image)
        except Exception:  # noqa: BLE001 - OCR is best-effort; never fail the page
            logger.exception("OCR failed for page %s", getattr(page, "page_number", "?"))
            return ""

    def _ocr_image(self, image: Image.Image) -> str:
        """Preprocess (grayscale + Otsu) then OCR a single image.

        Args:
            image: A PIL RGB image of one page.

        Returns:
            Recognised text.
        """
        array = np.array(image)
        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        # Otsu thresholding picks the binarisation threshold automatically,
        # which is robust to varied scan brightness.
        _, binarised = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        return str(pytesseract.image_to_string(binarised))

    @staticmethod
    def _normalise_headers(text: str) -> str:
        """Insert a blank line ahead of recognised section headers.

        This guarantees that the ``"\\n\\n"`` separator in the splitter aligns
        with the document's logical structure even when the source PDF runs
        headers inline with body text.

        Args:
            text: Raw page text.

        Returns:
            Text with a double-newline prepended to each detected header.
        """
        return _SECTION_HEADER_RE.sub(r"\n\n\g<hdr>", text)
