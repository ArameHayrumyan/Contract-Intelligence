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
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from rag_core.config import LLMProviderFactory, Settings
from rag_core.engine import AuditEngine
from rag_core.ingestion_queue import IngestionQueue, IngestJob, InProcessIngestionQueue
from rag_core.processor import DocumentProcessor
from rag_core.schemas import (
    ContractAuditSchema,
    DocumentRecord,
    DocumentStatus,
    QAResponse,
)
from rag_core.storage import TenantVectorStore

logger = logging.getLogger("rag_core.api.service")


class DocumentNotFoundError(KeyError):
    """Raised when a document id is unknown for the requesting tenant."""


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
        queue: IngestionQueue | None = None,
        synchronous: bool = False,
    ) -> None:
        """Build the service container.

        Args:
            settings: Application settings.
            store: Optional vector store (tests inject an ephemeral one).
            engine: Optional pre-built engine (tests inject a fake LLM).
            queue: Optional ingestion queue override.
            synchronous: If ``True``, ingestion runs inline (used in tests for
                deterministic behaviour).
        """
        self._settings = settings
        self._processor = DocumentProcessor(settings)
        self._store = store or TenantVectorStore(settings)

        if engine is not None:
            self._engine = engine
        else:
            llm = LLMProviderFactory(settings).build()
            self._engine = AuditEngine(settings=settings, store=self._store, llm=llm)

        # Tenant-scoped registry: (tenant_id, document_id) -> record.
        self._registry: dict[tuple[str, str], DocumentRecord] = {}
        # Raw bytes awaiting processing, keyed identically. Cleared after ingest.
        self._pending: dict[tuple[str, str], bytes] = {}
        self._lock = threading.Lock()

        self._executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="ingest"
        )
        runner = self._make_runner(synchronous)
        self._queue: IngestionQueue = queue or InProcessIngestionQueue(
            job=self._ingest_job, runner=runner
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
            result = self._processor.process(
                data=data, document_id=document_id, tenant_id=tenant_id
            )
            self._store.add_chunks(tenant_id, result.chunks)
        except Exception as exc:  # noqa: BLE001 - record failure on the document
            self._fail(key, str(exc))
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)

        with self._lock:
            record.chunk_count = len(result.chunks)
            record.status = DocumentStatus.READY
            record.error = None
        logger.info(
            "Ingestion finished tenant=%s document=%s chunks=%d",
            tenant_id,
            document_id,
            len(result.chunks),
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

    def shutdown(self) -> None:
        """Cleanly stop the ingestion executor (called on app shutdown)."""
        self._executor.shutdown(wait=False, cancel_futures=True)
