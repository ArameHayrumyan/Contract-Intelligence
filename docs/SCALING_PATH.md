# Scaling Path — Demo → Enterprise

This document enumerates exactly what changes when this project graduates from a
single-tenant demo to a real multi-tenant enterprise deployment. Each seam was
built deliberately (Section 1) so that the swap touches **one module**, not the
routers or business logic.

| Concern | Today (demo) | Enterprise | Files that change |
| --- | --- | --- | --- |
| **Auth** | Stub `X-API-Key` → (user, tenant) map | OIDC / SSO (Auth0, Okta, Azure AD): validate JWT, read `sub` + `tenant` claims | `apps/api/dependencies.py` only — router signatures (`CurrentUserDep`, `TenantIdDep`) are unchanged |
| **Ingestion queue** | In-process `IngestionQueue` on a `ThreadPoolExecutor` | Celery + Redis (or SQS) workers | `packages/rag_core/rag_core/ingestion_queue.py` implementation only — routers depend on the `IngestionQueue` protocol |
| **Vector store** | Single-node persistent Chroma | Chroma server mode, or managed Qdrant / Weaviate Cloud | `packages/rag_core/rag_core/storage.py` only — the `tenant_{id}_contracts` scoping and metadata filtering are preserved verbatim |
| **Audit database** | Async SQLite via SQLAlchemy Core (`aiosqlite`) | Managed PostgreSQL (RDS / Cloud SQL) | `packages/rag_core/rag_core/database.py` only — change the connection URL to `postgresql+asyncpg://…` and the dialect import; the explicit `Table` definitions and all tenant-scoped queries are unchanged. Using Core (not the ORM) keeps the schema readable in one file and the swap a connection-string change |
| **Keyword index (BM25)** | In-process per-tenant BM25, rebuilt from the Chroma corpus on ingestion | Dedicated search service (OpenSearch / Elasticsearch) alongside the managed vector store — not before, since that is unnecessary infrastructure at current scale | `packages/rag_core/rag_core/storage.py` only — `bm25_query` returns the same `RetrievedChunk` shape, so the engine's RRF fusion is unchanged |
| **Standards store** | Standards in a per-tenant Chroma collection with an in-process BM25 index, mirroring contracts | Moves with the vector store + keyword index swaps above (the standards collection and its BM25 index are managed exactly like contracts) | `packages/rag_core/rag_core/storage.py` only |
| **Cross-reference run** | Synchronous `POST /documents/{id}/cross-reference` (multi-phase, 15-30s on free tier) | **First thing to background at production scale**: enqueue via the existing `IngestionQueue` (Celery/Redis) and poll for the result, so the request returns immediately | `apps/api/routers/crossref.py` + `apps/api/service.py` — the engine itself is already async and batched |
| **Secrets** | `.env` file | Vault / AWS Secrets Manager / Azure Key Vault | `packages/rag_core/rag_core/config.py` loader — `Settings` field names stay the same |
| **Observability** | Structured logs + `X-Request-ID` correlation | OpenTelemetry traces + metrics → Grafana / Prometheus | `apps/api/middleware/request_context.py` + instrumentation; the request-id is already the trace seed |
| **LLM provider** | Free tier in dev, paid in prod (env-gated) | Same gate; add providers to `LLMProvider` + factory | `packages/rag_core/rag_core/config.py` `LLMProviderFactory.build` |
| **Compliance** | Audit results cached in memory | Persisted audit-log table (Postgres) recording every audit run, its inputs, and the model id/version, for legal traceability | New `storage`-adjacent module + a write in `apps/api/service.py` |

## Invariants that never relax (Section 1 / Section 8)

These are fixed requirements, not defaults to optimise away:

1. **API/UI separation** — the frontend never imports or calls `rag_core`; it
   only reaches the FastAPI service (via the Next.js same-origin proxy).
2. **Tenant-scoped data** — no table, collection, or cache key omits a
   `tenant_id`. Tenant identity is derived from the authenticated principal,
   never a client-supplied value.
3. **Clause provenance** — every critical clause carries `source_chunk_id` +
   `page_number`; page numbers are reconciled from retrieval metadata so they
   cannot be hallucinated.
4. **Environment-gated provider selection** — production refuses to boot on a
   free-tier provider (`ConfigurationError` at startup).

## Horizontal scale notes

- The API image currently runs a **single uvicorn worker** because the
  in-process queue and node-local Chroma persistence assume one process. Moving
  to multiple workers/replicas is unlocked precisely by the **queue** and
  **vector store** swaps above — do them together.
- Embeddings (`BAAI/bge-small-en-v1.5`) run locally today. At scale, move them
  behind a dedicated embedding service or the managed store's native embeddings
  to free API memory.

## Compliance activity log

- The `activity_log` table is **append-only by design**: there is no UPDATE or
  DELETE function for it anywhere in `database.py` (a static test asserts this),
  and the only writer is `insert_activity` / `_log`. Not even the tenant can
  delete a log entry through the API.
- At production scale this moves to an **immutable / write-once audit store** —
  AWS CloudTrail, Azure Monitor, or a Postgres table with a trigger that rejects
  `UPDATE`/`DELETE`. The constraint is architectural (no mutation path exists),
  not merely "we didn't build the endpoint," so the swap is additive.
- Mutations write their log entry **in the same transaction** as the mutation
  (`_log(conn, …)`), so an action and its audit record commit atomically.

## Bulk operations

- **Bulk export** is capped at **20 documents** and generates PDFs synchronously,
  zipped in memory. That is realistic on the current Droplet; beyond it, move PDF
  generation to a background task (Celery) that writes the zip to object storage
  and returns a signed download link. **Bulk status** is capped at 50.

## Document parsing

- **If document types expand beyond PDF** (invoices, emails, HTML exports):
  replace the tiered `DocumentParser` with `unstructured` deployed as an
  **isolated sidecar container** — never inline in the API process. Its ML models
  (detectron2, paddleocr) add 3-4 GB RAM; isolating them prevents OOM on the main
  API container (which also runs FastAPI, Chroma, and the embedding model). The
  parser is already behind a single class (`DocumentParser.parse`) returning a
  `ParsedDocument`, so the swap is one module plus a network hop.
- **Licensing:** PyMuPDF is **AGPL-3.0**. Fine for an internal/demo deployment;
  for commercial redistribution either obtain a PyMuPDF commercial license or
  replace the multi-column block extraction with a permissively-licensed
  alternative (e.g. `pdfminer.six` layout analysis).
