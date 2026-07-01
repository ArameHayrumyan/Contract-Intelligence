"""Tests for the durable document/standard registry (synchronous store)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_core import registry_store


@pytest.fixture()
def registry(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Initialise an isolated registry on a temp SQLite file."""
    registry_store.init_registry(f"sqlite:///{(tmp_path / 'reg.db').as_posix()}")
    yield
    registry_store.dispose_registry()


def test_document_round_trip_and_status(registry: None) -> None:
    """A document is stored, tenant-scoped, and its status updates persist."""
    registry_store.insert_document(
        document_id="doc-1",
        tenant_id="acme",
        filename="contract.pdf",
        status="pending",
        page_count=3,
    )
    row = registry_store.get_document("acme", "doc-1")
    assert row is not None
    assert row["filename"] == "contract.pdf"
    assert row["status"] == "pending"

    registry_store.update_document(
        tenant_id="acme", document_id="doc-1", status="ready", chunk_count=12
    )
    updated = registry_store.get_document("acme", "doc-1")
    assert updated is not None
    assert updated["status"] == "ready"
    assert updated["chunk_count"] == 12


def test_document_is_tenant_scoped(registry: None) -> None:
    """Another tenant cannot read a document by id."""
    registry_store.insert_document(
        document_id="doc-a",
        tenant_id="tenant-a",
        filename="a.pdf",
        status="ready",
        page_count=1,
    )
    assert registry_store.get_document("tenant-b", "doc-a") is None


def test_standards_listed_sorted_and_scoped(registry: None) -> None:
    """Standards are returned for the tenant only, sorted by name then version."""
    registry_store.insert_standard(
        standard_document_id="s2",
        tenant_id="acme",
        standard_name="Policy",
        standard_version="2.0",
        status="ready",
    )
    registry_store.insert_standard(
        standard_document_id="s1",
        tenant_id="acme",
        standard_name="Policy",
        standard_version="1.0",
        status="ready",
    )
    registry_store.insert_standard(
        standard_document_id="other",
        tenant_id="globex",
        standard_name="Other",
        standard_version="1.0",
        status="ready",
    )

    listed = registry_store.list_standards("acme")
    assert [s["standard_version"] for s in listed] == ["1.0", "2.0"]
    assert all(s["tenant_id"] == "acme" for s in listed)
    assert registry_store.get_standard("globex", "s1") is None
