"""Background ingestion behind an interface.

Routers depend on the :class:`IngestionQueue` *protocol*, never a concrete
implementation. The demo ships :class:`InProcessIngestionQueue` (FastAPI
``BackgroundTasks`` — no Redis/Celery infra). Swapping to Celery+Redis at scale
touches only this file (Section 3.7 / ``docs/SCALING_PATH.md``).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from rag_core.schemas import DocumentStatus

logger = logging.getLogger("rag_core.ingestion_queue")

#: A unit of work: given (document_id, tenant_id), perform ingestion.
IngestJob = Callable[[str, str], None]


@runtime_checkable
class IngestionQueue(Protocol):
    """Contract for enqueuing and tracking document ingestion jobs."""

    def enqueue(self, document_id: str, tenant_id: str) -> None:
        """Schedule ingestion for a document.

        Args:
            document_id: The document to ingest.
            tenant_id: The owning tenant.
        """
        ...

    def get_status(self, document_id: str, tenant_id: str) -> DocumentStatus:
        """Return the current ingestion status for a document.

        Args:
            document_id: The document to query.
            tenant_id: The owning tenant.

        Returns:
            The document's current :class:`DocumentStatus`.
        """
        ...


class InProcessIngestionQueue:
    """In-process queue using a background-task runner.

    The actual ingestion work (``job``) is injected, keeping this module free of
    any dependency on the processing/storage layers. The API layer wires a
    FastAPI ``BackgroundTasks`` runner in via ``runner``; tests can pass a
    synchronous runner for determinism.
    """

    def __init__(
        self,
        *,
        job: IngestJob,
        runner: Callable[[IngestJob, str, str], None],
    ) -> None:
        """Initialise the queue.

        Args:
            job: The ingestion callable invoked as ``job(document_id, tenant_id)``.
            runner: Schedules ``job`` for execution. In the API this delegates to
                ``BackgroundTasks.add_task``; in tests it may run synchronously.
        """
        self._job = job
        self._runner = runner
        self._status: dict[tuple[str, str], DocumentStatus] = {}
        self._lock = threading.Lock()

    def enqueue(self, document_id: str, tenant_id: str) -> None:
        """Mark the document pending and schedule the wrapped job.

        Args:
            document_id: The document to ingest.
            tenant_id: The owning tenant.
        """
        key = (tenant_id, document_id)
        with self._lock:
            self._status[key] = DocumentStatus.PENDING
        logger.info("Enqueued ingestion tenant=%s document=%s", tenant_id, document_id)
        self._runner(self._wrapped_job, document_id, tenant_id)

    def _wrapped_job(self, document_id: str, tenant_id: str) -> None:
        """Run the job, maintaining status transitions and error capture.

        Args:
            document_id: The document being ingested.
            tenant_id: The owning tenant.
        """
        key = (tenant_id, document_id)
        with self._lock:
            self._status[key] = DocumentStatus.PROCESSING
        try:
            self._job(document_id, tenant_id)
        except Exception:  # noqa: BLE001 - background boundary; must not crash worker
            with self._lock:
                self._status[key] = DocumentStatus.FAILED
            logger.exception(
                "Ingestion failed tenant=%s document=%s", tenant_id, document_id
            )
            return
        with self._lock:
            self._status[key] = DocumentStatus.READY
        logger.info("Ingestion ready tenant=%s document=%s", tenant_id, document_id)

    def get_status(self, document_id: str, tenant_id: str) -> DocumentStatus:
        """Return the tracked status, defaulting to ``PENDING`` if unknown.

        Args:
            document_id: The document to query.
            tenant_id: The owning tenant.

        Returns:
            The document's current :class:`DocumentStatus`.
        """
        with self._lock:
            return self._status.get((tenant_id, document_id), DocumentStatus.PENDING)
