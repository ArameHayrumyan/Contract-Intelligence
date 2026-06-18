"""Tests for document processing: header normalisation, chunking, provenance."""

from __future__ import annotations

import pytest

from rag_core.config import get_settings
from rag_core.processor import DocumentProcessor, PageText


@pytest.fixture()
def processor() -> DocumentProcessor:
    """A processor built from test settings."""
    return DocumentProcessor(get_settings())


def test_normalise_headers_inserts_breaks(processor: DocumentProcessor) -> None:
    """Section headers gain a preceding blank line for reliable splitting."""
    text = "Some preamble. Article 5 Termination follows. §7 Liability."
    normalised = processor._normalise_headers(text)
    assert "\n\nArticle 5" in normalised
    assert "\n\n§7" in normalised


def test_normalise_headers_idempotent_on_existing_break(
    processor: DocumentProcessor,
) -> None:
    """A header already preceded by a newline is not double-broken."""
    text = "intro\nArticle 1 scope"
    normalised = processor._normalise_headers(text)
    # The lookbehind prevents inserting a second break before the existing \n.
    assert normalised.count("Article 1") == 1


@pytest.mark.parametrize(
    ("native", "expected_ocr"),
    [("", True), ("short", True), ("x" * 200, False)],
)
def test_needs_ocr_threshold(
    processor: DocumentProcessor, native: str, expected_ocr: bool
) -> None:
    """OCR is triggered only when native text falls below the threshold."""
    assert processor._needs_ocr(native) is expected_ocr


def test_process_emits_provenance(
    processor: DocumentProcessor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every chunk carries chunk_id, page_number and tenant_id."""
    pages = [
        PageText(
            page_number=1,
            text="Article 1\n" + ("clause text " * 300),
            used_ocr=False,
        )
    ]
    monkeypatch.setattr(processor, "_extract_pages", lambda data: pages)

    result = processor.process(
        data=b"%PDF-fake", document_id="doc-1", tenant_id="acme"
    )

    assert result.chunks, "expected at least one chunk"
    for chunk in result.chunks:
        assert chunk.chunk_id
        assert chunk.document_id == "doc-1"
        assert chunk.tenant_id == "acme"
        assert chunk.page_number == 1


def test_process_raises_when_no_text(
    processor: DocumentProcessor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A document yielding no extractable text raises ValueError."""
    monkeypatch.setattr(
        processor,
        "_extract_pages",
        lambda data: [PageText(page_number=1, text="   ", used_ocr=True)],
    )
    with pytest.raises(ValueError, match="No extractable text"):
        processor.process(data=b"%PDF-fake", document_id="d", tenant_id="t")
