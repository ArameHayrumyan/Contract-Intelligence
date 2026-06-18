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
| **Keyword index (BM25)** | In-process per-tenant BM25, rebuilt from the Chroma corpus on ingestion | Dedicated search service (OpenSearch / Elasticsearch) alongside the managed vector store — not before, since that is unnecessary infrastructure at current scale | `packages/rag_core/rag_core/storage.py` only — `bm25_query` returns the same `RetrievedChunk` shape, so the engine's RRF fusion is unchanged |
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
