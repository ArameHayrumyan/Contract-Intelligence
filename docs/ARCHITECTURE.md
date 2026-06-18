# Architecture

## Design center

This is a production-quality demo engineered to scale into a real enterprise
deployment **without architectural rework**. Every cross-cutting seam (auth,
multi-tenancy, LLM provider, background processing) exists as a real interface
today, even where the implementation behind it is intentionally lightweight.

## Component map

```
Browser
  │  (same-origin, access-cookie gated)
  ▼
Next.js (apps/web)
  │  server-side proxy routes attach the backend API key
  ▼
FastAPI (apps/api)  ── the ONLY consumer of rag_core
  │
  ▼
rag_core (packages/rag_core)
  ├─ config      Settings + environment-gated LLMProviderFactory
  ├─ security    size cap · MIME sniff · page cap (pre-parse)
  ├─ processor   pdfplumber → OCR failover → hierarchy-aware chunking
  ├─ storage     tenant-scoped Chroma collections + per-tenant BM25 index
  ├─ engine      multi-query expansion · hybrid (vector+BM25) RRF (k=60) · structured output
  ├─ ingestion_queue   IngestionQueue protocol + in-process impl
  └─ schemas     ContractAuditSchema with per-clause provenance
```

## Request lifecycles

### Upload → ingestion
1. `POST /documents` (rate-limited; auth → tenant).
2. `security.validate_upload` runs **before** any parsing (size, MIME, pages).
3. `ContractService.register_upload` records a tenant-scoped `DocumentRecord`
   and enqueues ingestion.
4. The queue runs `processor.process` (native parse → OCR failover →
   chunking) and persists chunks via `TenantVectorStore.add_chunks`. Each chunk
   carries `chunk_id` + `page_number` + `tenant_id`.
5. `GET /documents/{id}` reports status (`pending` → `processing` → `ready`).

### Audit
1. `GET /documents/{id}/audit` (computed lazily, cached).
2. `engine.audit_document` expands the intent into 3 variants (compliance,
   financial, termination). Retrieval is **hybrid**: each variant runs both a
   top-10 vector (semantic) search and a top-10 BM25 (keyword) search, so RRF
   fuses **6 ranked lists** (k=60), and the top 5 fused chunks are kept.
3. The LLM is called with `.with_structured_output(ContractAuditSchema)` under
   `tenacity` retry (rate-limit resilient).
4. Provenance is reconciled: each clause's `page_number` is set from retrieval
   metadata so it cannot be hallucinated.

### Cross-document QA
`POST /qa` expands the question into 3 variants, runs the same hybrid
(vector + BM25) retrieval fused with RRF, and returns a grounded answer with
`QACitation`s (chunk id + page).

### Hybrid retrieval (why both)
Pure vector search smooths away exact lexical references — article/section
numbers (`Section 4.1.a`), defined terms, and precise figures — that lawyers
search for verbatim. BM25 keyword search recovers those; semantic search keeps
recall on paraphrased or conceptually-stated terms. Each tenant's BM25 index is
built in-process from the same chunk text persisted in Chroma (Chroma stays the
single source of truth) and rebuilt whenever the tenant's corpus changes during
ingestion. Both retrievers return identical `RetrievedChunk` shapes carrying
`chunk_id` + `page_number`, so they fuse through the **one** existing RRF
function — there is no second fusion algorithm.

## Cross-cutting concerns

- **Request correlation** — `RequestContextMiddleware` mints an `X-Request-ID`,
  binds it to the logging context, and echoes it in the response. This is the
  seed for OpenTelemetry tracing later.
- **Logging** — rotating file handler (10 MB × 5) + stdout, with a `request_id`
  field on every record.
- **Cost-abuse controls (Section 6)** — application access-code gate (web
  middleware), per-IP `slowapi` limits on `/documents` and `/qa`, and a
  provider-side spend cap (operational, set in the provider dashboard).

## Why these choices (flexibility clause, Section 8)

- **ThreadPoolExecutor over FastAPI `BackgroundTasks`** for ingestion: OCR is
  CPU-heavy; a dedicated pool gives true fire-and-forget independent of the
  response cycle and is a faithful stand-in for the Celery worker it becomes.
  The swap still touches only `ingestion_queue.py`.
- **Server-side proxy in the web app** rather than browser-direct API calls:
  keeps the backend key off the client and enforces the access gate on the only
  public path, upholding API/UI separation end to end.
- **Regex header pre-pass + `\n\n` separator** rather than literal header
  separators: respects document hierarchy even when headers run inline.

See [SCALING_PATH.md](SCALING_PATH.md) for the demo → enterprise swap list.
