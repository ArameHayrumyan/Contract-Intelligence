"""Upload validation: the first line of defence against malicious uploads.

Every check here runs *before* a single byte is parsed: a hard size cap, a
magic-byte MIME sniff (so a renamed ``.exe`` cannot masquerade as a PDF), and a
page-count cap (so a "PDF bomb" cannot exhaust OCR/CPU). Any failure rejects the
upload outright (Architectural Constraint, Section 3.2).
"""

from __future__ import annotations

import io
import logging

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from rag_core.config import Settings

logger = logging.getLogger("rag_core.security")

#: Leading bytes that every valid PDF must begin with.
_PDF_MAGIC = b"%PDF-"


class UploadValidationError(ValueError):
    """Raised when an uploaded file fails a security precondition.

    Attributes:
        reason: A short machine-stable reason code suitable for API responses.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def _sniff_is_pdf(data: bytes) -> bool:
    """Return whether ``data`` begins with the PDF magic bytes.

    We sniff content rather than trusting the filename extension. ``python-magic``
    is used when available for a more authoritative MIME read; we fall back to a
    direct magic-byte comparison so validation never depends on a native libmagic
    install being present.

    Args:
        data: The raw file bytes (at least the first few bytes).

    Returns:
        ``True`` if the content looks like a PDF.
    """
    try:
        import magic

        mime = magic.from_buffer(data[:2048], mime=True)
        return bool(mime == "application/pdf")
    except Exception:  # pragma: no cover - libmagic optional / platform-specific
        logger.debug("libmagic unavailable; falling back to magic-byte sniff")
        return data[: len(_PDF_MAGIC)] == _PDF_MAGIC


def validate_upload(
    *,
    data: bytes,
    filename: str,
    settings: Settings,
) -> int:
    """Validate an uploaded file and return its page count.

    Performs, in order: size cap, MIME sniff, and page-count cap. The page count
    is returned so the caller can record it without re-parsing.

    Args:
        data: Raw uploaded bytes.
        filename: Original filename (used only for logging/diagnostics).
        settings: Application settings supplying the caps.

    Returns:
        The number of pages in the validated PDF.

    Raises:
        UploadValidationError: If any check fails. ``reason`` is one of
            ``"empty"``, ``"too_large"``, ``"not_pdf"``, ``"unreadable"``,
            or ``"too_many_pages"``.
    """
    size = len(data)
    if size == 0:
        raise UploadValidationError("Uploaded file is empty.", reason="empty")

    if size > settings.max_upload_bytes:
        raise UploadValidationError(
            f"File is {size} bytes; max allowed is {settings.max_upload_bytes}.",
            reason="too_large",
        )

    if not _sniff_is_pdf(data):
        raise UploadValidationError(
            "File content is not a PDF (magic-byte sniff failed).",
            reason="not_pdf",
        )

    try:
        reader = PdfReader(io.BytesIO(data))
        page_count = len(reader.pages)
    except (PdfReadError, OSError, ValueError) as exc:
        raise UploadValidationError(
            f"PDF could not be parsed: {exc}", reason="unreadable"
        ) from exc

    if page_count == 0:
        raise UploadValidationError("PDF has zero pages.", reason="unreadable")

    if page_count > settings.max_pages:
        raise UploadValidationError(
            f"PDF has {page_count} pages; max allowed is {settings.max_pages}.",
            reason="too_many_pages",
        )

    logger.info(
        "Upload validated: filename=%s size=%d pages=%d",
        filename,
        size,
        page_count,
    )
    return page_count
