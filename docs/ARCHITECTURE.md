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
  ├─ processor   tiered parsing: layout classify → tables + text → chunking
  ├─ storage     tenant-scoped Chroma collections (contracts + standards) + BM25
  ├─ engine      multi-query expansion · hybrid (vector+BM25) RRF (k=60) · structured output
  ├─ engine_xref cross-reference: clause inventory → align → classify → score
  ├─ ingestion_queue   IngestionQueue protocol + in-process impl
  ├─ database    async SQLite (SQLAlchemy Core) audit/crossref/settings store
  ├─ report_generator  reportlab PDF reports (single-doc + portfolio)
  ├─ schemas     ContractAuditSchema with per-clause provenance
  └─ schemas_xref  CrossReferenceAuditSchema + ClauseDeviation
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

### Cross-reference (contract vs. corporate standard)

A parallel workflow that compares a subject contract against a versioned
corporate **standard**, clause by clause. It does not touch `ContractAuditSchema`
or the `/audit` endpoint.

- **Standards storage** — standards live in a separate per-tenant collection
  (`tenant_{id}_standards`) with their own BM25 index. They are **append-only and
  versioned**: a new upload of the same standard name creates a new
  `standard_document_id` / version; old versions stay queryable.
- **Why clause alignment is the hard part** — two documents won't share numbering
  ("Section 4.1" ↔ "Article 12.3"), so a naïve vector search misses the pair. The
  engine solves this in four explicit phases:
  1. **Inventory** — the LLM extracts a *normalized* clause inventory from each
     document (clause type → text), independent of original numbering.
  2. **Align** — for each subject clause, hybrid search (BM25 + vector, the same
     RRF, k=60) retrieves the standard's counterpart; the standard is likewise
     searched for clauses **missing** from the subject. The fused RRF score is
     thresholded (`XREF_MIN_RRF_SCORE`) to decide "no counterpart."
  3. **Classify** — each aligned pair is classified into a `DeviationType`
     (missing / weakened / strengthened / contradictory / unaddressed) with a
     severity, via structured generation. Calls are concurrent but capped
     (`XREF_MAX_CONCURRENCY`) to protect free-tier rate limits.
  4. **Score** — `overall_risk_score` is computed **programmatically** from the
     deviations (severity × type-weight average via `DEVIATION_WEIGHTS`), then a
     short executive summary is generated.
- **Provenance on both sides** — every deviation carries the subject's chunk id +
  page and (when present) the standard's chunk id + page, reconciled from
  retrieval metadata.
- **Endpoints** — `POST /standards` (versioned upload, queued ingestion),
  `GET /standards` (grouped list), `POST /documents/{id}/cross-reference`
  (synchronous for the demo; both ids validated against the caller's tenant —
  cross-tenant reference returns 403).

## Document parsing (tiered extraction)

PDFs vary wildly — clean native text, two-column legal layouts, bordered tables,
and scans. A single extractor handles none of them well, so `processor.py` runs a
**tiered strategy**, deliberately **not** `unstructured` (whose detectron2 /
paddleocr models add 3-4 GB RAM and OOM the Droplet). The CPU-only toolchain
(pymupdf + camelot + img2table) adds well under ~0.5 GB.

**Per-page decision tree** (`DocumentParser.parse`):

1. **Classify layout** with pymupdf text blocks: a page is *multi-column* when
   two non-overlapping blocks span >70% of the width; *scanned* when there are no
   text blocks (or mean block text < 10 chars); else *single-column native*.
2. **Tables** (native pages): camelot **lattice** first (accuracy ≥ 85), falling
   back to **stream** (`edge_tol=50`). Lattice is tried first because ruled
   tables — the common case in contracts/SLAs — are detected far more reliably by
   line intersection than by whitespace heuristics.
3. **Text** per layout: pdfplumber for single-column (preserves reading order);
   **pymupdf block ordering** for multi-column (pdfplumber concatenates the two
   columns horizontally and garbles them); OCR (Otsu + pytesseract) for scans,
   with **img2table** additionally detecting tables in the page image.
4. **Header pre-pass** then chunking. Text uses the splitter (1200/250); **tables
   are never split** — a table is one chunk so its structure survives retrieval
   (a >1200-char table is logged, not truncated).

**Dual output.** Each table is stored with two representations: a
`markdown_representation` (the searchable `document` text, so semantic search and
the audit/QA prompts read human-readable rows) and `structured_data` (the raw
`[row][col]` grid, persisted in Chroma metadata). Cross-referencing uses the grid
for a deterministic **cell diff** before the LLM, so the model explains a real
comparison instead of hallucinating one over markdown.

## Audit persistence (dashboard / monitoring / export)

The audit endpoint used to re-run the LLM pipeline on every call — no history, no
data for a dashboard, nothing stable to export. `database.py` adds a durable
record so the three portfolio features read persisted data, never the engine.

- **Store** — `rag_core/database.py`, **SQLAlchemy Core** (explicit `Table`
  definitions, not the ORM) over **async SQLite** (`aiosqlite`). Tables:
  `audit_results`, `crossref_results`, `tenant_settings`. Every function is
  tenant-scoped — `tenant_id` is a required argument and always in the WHERE
  clause. Initialised eagerly in the API lifespan (not lazily on first request).
- **Write path** — `GET /documents/{id}/audit` persists via
  `upsert_audit_result` *after* generation; a storage failure is logged, never
  fails the audit response. A cross-reference run persists via
  `upsert_crossref_result` and flips `has_crossref` on the audit row. Re-audits
  preserve the human workflow status.
- **Read path** — the dashboard, monitoring, and export routers query
  `audit_results` directly. ChromaDB is for retrieval, never reporting.
- **Reports** — `report_generator.py` builds branded PDFs with **reportlab**
  Platypus (pure Python; WeasyPrint would need Cairo/Pango ~80 MB). Single-doc
  (cover → summary → clauses → optional cross-reference) and portfolio (cover →
  overview + bar chart → renewal windows → inventory). Exports stream persisted
  data — they never re-run the engine. The web proxy forwards PDF bytes through a
  dedicated binary path (`proxyBinary`) so they are not text-corrupted.

## Human annotations & the compliance activity log

Tier 2 adds reviewer annotations at three granularities and an immutable audit
trail of every mutation.

- **Annotations** (`annotations` table) attach a typed reviewer note to a
  *document*, a *clause* (by `chunk_id`), or a *deviation* (by `deviation_id`).
  Deletes are **soft** (`deleted_at`) — the row never leaves the database, so the
  record of what was once asserted survives. Deviations got stable ids via a new
  normalized `crossref_deviations` table (written alongside the existing
  cross-reference JSON blob, which is unchanged) so a note can target one.
- **Activity log** (`activity_log` table) is **append-only**. Every state
  mutation — audit run, cross-reference, status change, annotation add/edit/
  delete, single/portfolio/bulk export, bulk status — writes a log entry. The
  write happens **inside the same transaction as the mutation** (`_log(conn, …)`
  in the database layer, not the router), so it is impossible to mutate state
  without recording it. There is deliberately **no UPDATE/DELETE path** for the
  table (a static test enforces this); see `docs/SCALING_PATH.md` for the
  write-once production target.
- **Bulk operations** (dashboard) validate that every id belongs to the caller's
  tenant before touching anything (all-or-nothing, 403 on any foreign id). Bulk
  export streams an in-memory zip of per-document PDFs; a single failure becomes
  an `ERRORS.txt` entry rather than aborting the archive.

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
