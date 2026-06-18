# rag_core

Tenant-scoped contract intelligence & SLA auditing core.

This package contains **all business logic**. It is consumed exclusively by the
FastAPI service in `apps/api`. The frontend never imports or calls this package
directly (see Architectural Constraint #1).

## Modules

| Module               | Responsibility                                                       |
| -------------------- | ------------------------------------------------------------------- |
| `config`             | Settings + environment-gated `LLMProviderFactory`, logging setup.    |
| `security`           | Upload validation: size cap, MIME sniffing, page-count cap.          |
| `processor`          | Native parse → OCR failover → hierarchy-aware chunking.              |
| `schemas`            | `ContractAuditSchema` with per-clause provenance.                   |
| `storage`            | Tenant-scoped Chroma collection management.                         |
| `ingestion_queue`    | `IngestionQueue` protocol + in-process (Celery-ready) impl.         |
| `engine`             | Multi-query expansion, RRF fusion, structured generation.          |

## Design seams (see `docs/SCALING_PATH.md`)

Every cross-cutting concern that must change at enterprise scale lives behind a
single module so the swap touches one file: auth (`dependencies.py`), the queue
(`ingestion_queue.py`), the vector store (`storage.py`), and provider selection
(`config.py`).
