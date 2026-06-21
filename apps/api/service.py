"""Application service container: wires rag_core components for the API.

This is the *only* place the API touches ``rag_core`` business logic; routers
depend on this container, never on rag_core internals directly. It owns the
tenant-scoped document registry, the vector store, the audit engine, and the
ingestion queue.

Flexibility-clause note (Section 8): the in-process queue is driven by a
dedicated :class:`~concurrent.futures.ThreadPoolExecutor` rather than FastAPI
``BackgroundTasks``. Rationale — OCR is CPU-heavy; a dedicated pool gives true
fire-and-forget without tying job lifetime to a request's response cycle, and is
a more faithful stand-in for the out-of-process Celery worker it will become.
The swap still touches only ``ingestion_queue.py`` (Constraint #5).
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from rag_core.config import LLMProviderFactory, Settings
from rag_core.database import upsert_crossref_result
from rag_core.engine import AuditEngine
from rag_core.engine_xref import CrossReferenceEngine
from rag_core.ingestion_queue import IngestionQueue, IngestJob, InProcessIngestionQueue
from rag_core.processor import DocumentParser
from rag_core.schemas import (
    Chunk,
    ContractAuditSchema,
    DocumentRecord,
    DocumentStatus,
    QAResponse,
    TableElement,
    TextElement,
)
from rag_core.schemas_xref import CrossReferenceAuditSchema, StandardRecord
from rag_core.storage import TenantVectorStore

logger = logging.getLogger("rag_core.api.service")


class DocumentNotFoundError(KeyError):
    """Raised when a document id is unknown for the requesting tenant."""


class StandardNotFoundError(KeyError):
    """Raised when a standard id is unknown for (or not owned by) the tenant.

    The cross-reference router maps this to HTTP 403: a tenant must not be able
    to reference another tenant's standard even with a valid id.
    """


class ContractService:
    """Coordinates ingestion, auditing, and QA across rag_core components.

    All public methods take a ``tenant_id`` and enforce tenant scoping; a caller
    can never reach another tenant's documents.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        store: TenantVectorStore | None = None,
        engine: AuditEngine | None = None,
        xref_engine: CrossReferenceEngine | None = None,
        queue: IngestionQueue | None = None,
        synchronous: bool = False,
    ) -> None:
        """Build the service container.

        Args:
            settings: Application settings.
            store: Optional vector store (tests inject an ephemeral one).
            engine: Optional pre-built audit engine (tests inject a fake LLM).
            xref_engine: Optional pre-built cross-reference engine (tests inject).
            queue: Optional ingestion queue override.
            synchronous: If ``True``, ingestion runs inline (used in tests for
                deterministic behaviour).
        """
        self._settings = settings
        self._parser = DocumentParser(settings)
        self._store = store or TenantVectorStore(settings)

        # Build the LLM once only if a real engine is actually needed. The
        # assert narrows the optional for the type checker (it is always set
        # whenever an engine has to be constructed).
        built_llm = None
        if engine is None or xref_engine is None:
            built_llm = LLMProviderFactory(settings).build()
        if engine is not None:
            self._engine = engine
        else:
            assert built_llm is not None
            self._engine = AuditEngine(
                settings=settings, store=self._store, llm=built_llm
            )
        if xref_engine is not None:
            self._xref_engine = xref_engine
        else:
            assert built_llm is not None
            self._xref_engine = CrossReferenceEngine(
                settings=settings, store=self._store, llm=built_llm
            )

        # Tenant-scoped registries: (tenant_id, id) -> record.
        self._registry: dict[tuple[str, str], DocumentRecord] = {}
        self._standards: dict[tuple[str, str], StandardRecord] = {}
        # Raw bytes awaiting processing, keyed identically. Cleared after ingest.
        self._pending: dict[tuple[str, str], bytes] = {}
        # Standard pending payloads: key -> (bytes, name, version).
        self._standards_pending: dict[tuple[str, str], tuple[bytes, str, str]] = {}
        self._lock = threading.Lock()

        self._executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="ingest"
        )
        runner = self._make_runner(synchronous)
        self._queue: IngestionQueue = queue or InProcessIngestionQueue(
            job=self._ingest_job, runner=runner
        )
        # Separate queue instance for standards so status tracking does not
        # collide with the contracts queue (same id space, different worklists).
        self._standards_queue: IngestionQueue = InProcessIngestionQueue(
            job=self._ingest_standard_job, runner=runner
        )

    def _make_runner(self, synchronous: bool):  # type: ignore[no-untyped-def]
        """Return a runner that schedules ingestion jobs.

        Args:
            synchronous: Run inline when ``True``; otherwise on the thread pool.

        Returns:
            A callable ``(job, document_id, tenant_id) -> None``.
        """
        if synchronous:
            def run_now(job: IngestJob, document_id: str, tenant_id: str) -> None:
                job(document_id, tenant_id)

            return run_now

        def run_async(job: IngestJob, document_id: str, tenant_id: str) -> None:
            self._executor.submit(job, document_id, tenant_id)

        return run_async

    # --- Upload / ingestion --------------------------------------------------

    def register_upload(
        self, *, tenant_id: str, filename: str, data: bytes, page_count: int
    ) -> DocumentRecord:
        """Register a validated upload and enqueue it for ingestion.

        Args:
            tenant_id: The owning tenant.
            filename: Original upload filename.
            data: Validated raw PDF bytes.
            page_count: Page count from ``security.validate_upload``.

        Returns:
            The created :class:`DocumentRecord` (status ``PENDING``).
        """
        document_id = uuid.uuid4().hex
        key = (tenant_id, document_id)
        record = DocumentRecord(
            document_id=document_id,
            tenant_id=tenant_id,
            filename=filename,
            status=DocumentStatus.PENDING,
            page_count=page_count,
        )
        with self._lock:
            self._registry[key] = record
            self._pending[key] = data
        self._queue.enqueue(document_id, tenant_id)
        logger.info(
            "Registered upload tenant=%s document=%s filename=%s",
            tenant_id,
            document_id,
            filename,
        )
        return record

    def _ingest_job(self, document_id: str, tenant_id: str) -> None:
        """Process pending bytes into chunks and persist them.

        This is the unit of work handed to the queue. Status transitions are
        managed by the queue wrapper; here we update the registry record.

        Args:
            document_id: The document to ingest.
            tenant_id: The owning tenant.

        Raises:
            DocumentNotFoundError: If no pending bytes exist for the document.
        """
        key = (tenant_id, document_id)
        with self._lock:
            data = self._pending.get(key)
            record = self._registry.get(key)
        if data is None or record is None:
            raise DocumentNotFoundError(f"No pending data for {key}")

        self._set_status(key, DocumentStatus.PROCESSING)
        try:
            chunks = self._parse_to_chunks(data, document_id, tenant_id)
            self._store.add_chunks(tenant_id, chunks)
        except Exception as exc:  # noqa: BLE001 - record failure on the document
            self._fail(key, str(exc))
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)

        with self._lock:
            record.chunk_count = len(chunks)
            record.status = DocumentStatus.READY
            record.error = None
        logger.info(
            "Ingestion finished tenant=%s document=%s chunks=%d",
            tenant_id,
            document_id,
            len(chunks),
        )

    def _parse_to_chunks(
        self, data: bytes, document_id: str, tenant_id: str
    ) -> list[Chunk]:
        """Parse PDF bytes into persistable chunks via the tiered parser.

        Camelot reads from disk, so the validated bytes are written to a
        temporary file for the duration of parsing and removed afterwards.

        Args:
            data: Validated raw PDF bytes.
            document_id: Owning document (or standard) id.
            tenant_id: Owning tenant id.

        Returns:
            One :class:`Chunk` per parsed element (tables carry their grid).
        """
        handle, path = tempfile.mkstemp(suffix=".pdf")
        try:
            with os.fdopen(handle, "wb") as tmp:
                tmp.write(data)
            parsed = self._parser.parse(
                pdf_path=path, document_id=document_id, tenant_id=tenant_id
            )
        finally:
            try:
                os.remove(path)
            except OSError:
                logger.warning("Could not remove temp PDF %s", path)
        return [
            self._element_to_chunk(element, document_id)
            for element in parsed.elements
        ]

    @staticmethod
    def _element_to_chunk(
        element: TableElement | TextElement, document_id: str
    ) -> Chunk:
        """Convert a parsed element into a persistable :class:`Chunk`.

        Tables store their markdown as the searchable ``text`` and carry the
        grid (headers + cells) so cross-referencing can compare cells directly.
        ``document_id`` is threaded in because elements don't carry it.

        Args:
            element: A ``TableElement`` or ``TextElement`` from the parser.
            document_id: The owning document (or standard) id.

        Returns:
            The corresponding :class:`Chunk`.
        """
        if isinstance(element, TableElement):
            return Chunk(
                chunk_id=element.chunk_id,
                document_id=document_id,
                tenant_id=element.tenant_id,
                page_number=element.page_number,
                text=element.markdown_representation,
                element_type=element.element_type,
                extraction_method=element.extraction_method,
                column_headers=element.column_headers,
                structured_data=element.structured_data,
            )
        return Chunk(
            chunk_id=element.chunk_id,
            document_id=document_id,
            tenant_id=element.tenant_id,
            page_number=element.page_number,
            text=element.text,
            element_type=element.element_type,
            extraction_method=element.extraction_method,
        )

    def _set_status(self, key: tuple[str, str], status: DocumentStatus) -> None:
        """Update a record's status under lock."""
        with self._lock:
            record = self._registry.get(key)
            if record is not None:
                record.status = status

    def _fail(self, key: tuple[str, str], error: str) -> None:
        """Mark a record failed with an error message."""
        with self._lock:
            record = self._registry.get(key)
            if record is not None:
                record.status = DocumentStatus.FAILED
                record.error = error

    # --- Reads ---------------------------------------------------------------

    def get_document(self, *, tenant_id: str, document_id: str) -> DocumentRecord:
        """Return a tenant's document record.

        Args:
            tenant_id: The owning tenant.
            document_id: The document id.

        Returns:
            The :class:`DocumentRecord`.

        Raises:
            DocumentNotFoundError: If the document is unknown for the tenant.
        """
        with self._lock:
            record = self._registry.get((tenant_id, document_id))
        if record is None:
            raise DocumentNotFoundError(document_id)
        return record

    def get_audit(
        self, *, tenant_id: str, document_id: str
    ) -> ContractAuditSchema:
        """Return (computing and caching on first access) a document's audit.

        Args:
            tenant_id: The owning tenant.
            document_id: The document id.

        Returns:
            The :class:`ContractAuditSchema`.

        Raises:
            DocumentNotFoundError: If the document is unknown for the tenant.
            ValueError: If the document is not yet ``READY``.
        """
        record = self.get_document(tenant_id=tenant_id, document_id=document_id)
        if record.audit is not None:
            return record.audit
        if record.status is not DocumentStatus.READY:
            raise ValueError(
                f"Document {document_id} is not ready (status={record.status.value})."
            )
        audit = self._engine.audit_document(
            tenant_id=tenant_id, document_id=document_id
        )
        with self._lock:
            record.audit = audit
        return audit

    def answer_question(
        self,
        *,
        tenant_id: str,
        question: str,
        document_ids: list[str] | None = None,
    ) -> QAResponse:
        """Answer a cross-document question scoped to the tenant.

        Args:
            tenant_id: The owning tenant.
            question: The natural-language question.
            document_ids: Optional document subset.

        Returns:
            The :class:`QAResponse`.
        """
        return self._engine.answer_question(
            tenant_id=tenant_id, question=question, document_ids=document_ids
        )

    # --- Standards (cross-reference workflow) --------------------------------

    def register_standard_upload(
        self,
        *,
        tenant_id: str,
        data: bytes,
        standard_name: str,
        standard_version: str,
    ) -> StandardRecord:
        """Register a validated standard upload and enqueue its ingestion.

        Standards are append-only and versioned: this always creates a new
        ``standard_document_id`` and never overwrites a prior version.

        Args:
            tenant_id: The owning tenant.
            data: Validated raw PDF bytes.
            standard_name: Human-readable standard name (grouping key).
            standard_version: Version label.

        Returns:
            The created :class:`StandardRecord` (status ``pending``).
        """
        standard_document_id = uuid.uuid4().hex
        key = (tenant_id, standard_document_id)
        record = StandardRecord(
            standard_document_id=standard_document_id,
            standard_name=standard_name,
            standard_version=standard_version,
            tenant_id=tenant_id,
            status=DocumentStatus.PENDING.value,
        )
        with self._lock:
            self._standards[key] = record
            self._standards_pending[key] = (data, standard_name, standard_version)
        self._standards_queue.enqueue(standard_document_id, tenant_id)
        logger.info(
            "Registered standard tenant=%s standard=%s name=%s version=%s",
            tenant_id,
            standard_document_id,
            standard_name,
            standard_version,
        )
        return record

    def _ingest_standard_job(self, standard_document_id: str, tenant_id: str) -> None:
        """Process a pending standard upload into the standards collection.

        Args:
            standard_document_id: The standard to ingest.
            tenant_id: The owning tenant.

        Raises:
            StandardNotFoundError: If no pending payload exists.
        """
        key = (tenant_id, standard_document_id)
        with self._lock:
            payload = self._standards_pending.get(key)
            record = self._standards.get(key)
        if payload is None or record is None:
            raise StandardNotFoundError(f"No pending standard for {key}")
        data, name, version = payload

        with self._lock:
            record.status = DocumentStatus.PROCESSING.value
        try:
            chunks = self._parse_to_chunks(data, standard_document_id, tenant_id)
            self._store.add_standard_chunks(
                tenant_id,
                chunks,
                standard_version=version,
                standard_name=name,
            )
        except Exception as exc:  # noqa: BLE001 - record failure on the standard
            with self._lock:
                record.status = DocumentStatus.FAILED.value
                record.error = str(exc)
            raise
        finally:
            with self._lock:
                self._standards_pending.pop(key, None)

        with self._lock:
            record.chunk_count = len(chunks)
            record.status = DocumentStatus.READY.value
            record.error = None
        logger.info(
            "Standard ingested tenant=%s standard=%s chunks=%d",
            tenant_id,
            standard_document_id,
            len(chunks),
        )

    def list_standards(self, *, tenant_id: str) -> list[StandardRecord]:
        """Return all of the tenant's standard documents (all versions).

        Args:
            tenant_id: The owning tenant.

        Returns:
            Standard records, sorted by name then version.
        """
        with self._lock:
            records = [r for (t, _), r in self._standards.items() if t == tenant_id]
        return sorted(records, key=lambda r: (r.standard_name, r.standard_version))

    def get_standard(
        self, *, tenant_id: str, standard_document_id: str
    ) -> StandardRecord:
        """Return a tenant's standard record.

        Args:
            tenant_id: The owning tenant.
            standard_document_id: The standard id.

        Returns:
            The :class:`StandardRecord`.

        Raises:
            StandardNotFoundError: If unknown for (or not owned by) the tenant.
        """
        with self._lock:
            record = self._standards.get((tenant_id, standard_document_id))
        if record is None:
            raise StandardNotFoundError(standard_document_id)
        return record

    async def cross_reference(
        self,
        *,
        tenant_id: str,
        document_id: str,
        standard_document_id: str,
        actor: str = "system",
    ) -> CrossReferenceAuditSchema:
        """Cross-reference a contract against a standard, both tenant-scoped.

        Args:
            tenant_id: The caller's tenant.
            document_id: The subject contract.
            standard_document_id: The standard to compare against.
            actor: Who initiated the run (for the activity log).

        Returns:
            The :class:`CrossReferenceAuditSchema`.

        Raises:
            DocumentNotFoundError: If the subject document is unknown/not ready.
            StandardNotFoundError: If the standard is unknown for the tenant.
            ValueError: If either document is not yet ``READY``.
        """
        subject = self.get_document(tenant_id=tenant_id, document_id=document_id)
        standard = self.get_standard(
            tenant_id=tenant_id, standard_document_id=standard_document_id
        )
        if subject.status is not DocumentStatus.READY:
            raise ValueError(f"Document {document_id} is not ready for cross-reference.")
        if standard.status != DocumentStatus.READY.value:
            raise ValueError(f"Standard {standard_document_id} is not ready.")

        result = await self._xref_engine.run(
            subject_document_id=document_id,
            standard_document_id=standard_document_id,
            tenant_id=tenant_id,
        )
        # Persist for the dashboard/export (sets has_crossref on the audit row).
        # Best-effort: a storage failure must not fail the cross-reference.
        try:
            await upsert_crossref_result(result, tenant_id=tenant_id, actor=actor)
        except Exception:  # noqa: BLE001 - persistence is best-effort
            logger.exception(
                "Failed to persist cross-reference for document=%s", document_id
            )
        return result

    def chunk_exists(self, *, tenant_id: str, document_id: str, chunk_id: str) -> bool:
        """Whether a chunk id exists in a tenant's document (annotation guard).

        Args:
            tenant_id: The caller's tenant.
            document_id: The document to look within.
            chunk_id: The chunk id to verify.

        Returns:
            ``True`` if the chunk belongs to that tenant's document.
        """
        chunks = self._store.get_document_chunks(tenant_id, document_id)
        return any(c.chunk_id == chunk_id for c in chunks)

    def shutdown(self) -> None:
        """Cleanly stop the ingestion executor (called on app shutdown)."""
        self._executor.shutdown(wait=False, cancel_futures=True)
