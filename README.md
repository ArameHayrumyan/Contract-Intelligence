# Secure Contract Intelligence & SLA Auditor

A multi-tenant platform for **auditing commercial contracts at scale**. It ingests
contract PDFs (native text, complex tables, multi-column layouts, or scans via
OCR), produces a structured, **provenance-backed** risk assessment for each
contract, cross-references contracts against corporate standards clause-by-clause,
answers natural-language questions across a tenant's document set, and records
every action in an immutable compliance trail.

Built around an explicit set of architectural seams — auth, multi-tenancy, LLM
provider, background processing, persistence — each of which is a real interface,
so the single-node deployment scales to managed infrastructure by swapping one
module rather than rewriting. See
[docs/SCALING_PATH.md](docs/SCALING_PATH.md).

## Capabilities

- **Contract audit** — structured risk scoring with per-clause provenance
  (`source_chunk_id` + page), vendor / contract-type / auto-renewal / notice /
  liability-cap / end-date extraction.
- **Hybrid retrieval RAG** — BM25 keyword + vector search fused with Reciprocal
  Rank Fusion, so exact references (e.g. "Section 4.1.a") and paraphrased
  concepts both surface.
- **Cross-reference auditing** — aligns a contract against a versioned corporate
  standard clause-by-clause and classifies each deviation (missing / weakened /
  strengthened / contradictory / unaddressed) with severity.
- **Portfolio dashboard** — stats, filtering, sorting, bulk status changes, and
  bulk export, computed from persisted data.
- **SLA & renewal monitoring** — configurable expiry windows and auto-renewal
  alerts.
- **Human-in-the-loop** — reviewer annotations at document, clause, and
  deviation level; a workflow status per contract.
- **Immutable activity log** — every audit, cross-reference, status change,
  annotation, and export is recorded append-only for compliance.
- **Branded PDF reporting** — single-document and portfolio reports.
- **Cross-document Q&A** — grounded answers with clickable citations.

## Architecture at a glance

```
Browser ─(same-origin, access-gated)─▶ Next.js (apps/web)
                                         │  server-side proxy attaches the API key
                                         ▼
                                       FastAPI (apps/api) ── the ONLY consumer of rag_core
                                         ▼
                                       rag_core (packages/rag_core)
```

```
secure-contract-intelligence/
├── apps/
│   ├── api/        FastAPI service — routers, auth deps, request-context mw
│   └── web/        Next.js (App Router) + TypeScript — design system + pages,
│                   talks only to its own same-origin proxy
├── packages/
│   └── rag_core/   business logic:
│                   config · security · processor (tiered PDF parsing) · storage
│                   (tenant Chroma + BM25) · engine (hybrid RAG) · engine_xref
│                   (cross-reference) · registry_store (sync doc/standard registry)
│                   · database (async audit/compliance) · report_generator · schemas
├── deploy/docker/  dev + prod compose, Caddy reverse proxy (TLS)
├── tests/          pytest — processor, engines, API, persistence, compliance
└── docs/           ARCHITECTURE.md · SCALING_PATH.md
```

## Tech stack

| Layer | Technology |
| --- | --- |
| API | FastAPI, Pydantic v2, SQLAlchemy Core |
| Persistence | SQLite by default · **PostgreSQL via `DATABASE_URL`** (async `asyncpg` / sync `psycopg`) |
| Vector store | ChromaDB (per-tenant collections) + in-process BM25 |
| Embeddings | `BAAI/bge-small-en-v1.5` (local, no external call) |
| Parsing | pdfplumber · pymupdf · camelot · img2table · pytesseract (OCR) |
| LLM | Pluggable: OpenAI / Anthropic / Azure OpenAI / Groq / Gemini (env-gated) |
| Web | Next.js App Router, TypeScript, token-based design system |
| Infra | Docker Compose, Caddy (TLS), GitHub Actions CI/CD |

## Quick start

```bash
cp .env.example .env          # set a dev LLM provider key, e.g. GROQ_API_KEY
docker compose -f deploy/docker/docker-compose.yml up --build
```

- Web UI: http://localhost:3000 (enter the `ACCESS_CODE` from `.env`)
- API docs: http://localhost:8000/docs

### Local development (without Docker)

```bash
# Backend
pip install -e "packages/rag_core[dev]"
pip install -r apps/api/requirements.txt
cd apps/api && uvicorn main:app --reload

# Frontend (separate shell)
cd apps/web && npm install && npm run dev
```

## Configuration

Set via `.env` (see [.env.example](.env.example)). Key variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `ENVIRONMENT` | `dev` | `dev` / `staging` / `production` (gates the LLM provider) |
| `LLM_PROVIDER` | `groq_free` | Active provider; production refuses free tiers |
| `DATABASE_URL` | _(unset → SQLite)_ | Point the whole persistence layer at PostgreSQL |
| `SQLITE_DB_PATH` | `data/audits/audit_store.db` | Local DB file when `DATABASE_URL` is unset |
| `CHROMA_PERSIST_DIR` | `data/chroma` | Vector-store persistence directory |
| `ACCESS_CODE` | _(required)_ | Shared application access code (web gate) |
| `API_KEY` | _(required)_ | Backend key the web proxy attaches server-side |

**Switching to PostgreSQL:** `pip install "rag_core[postgres]"` and set
`DATABASE_URL=postgresql://user:pass@host:5432/db`. No code change — the
async/sync driver variants are derived automatically.

## Persistence & durability

All user data survives a restart: the **document/standard registry**
(`registry_store.py`), **audit results, annotations, and the activity log**
(`database.py`), and the **vector index** (Chroma on disk). Both database layers
use SQLAlchemy Core (readable schema, no ORM) and switch to PostgreSQL via
`DATABASE_URL` with no code change.

## Testing & quality

```bash
ruff check packages/rag_core apps/api tests
mypy packages/rag_core/rag_core apps/api
pytest -q
cd apps/web && npm run lint && npm run typecheck
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs all of the above
on every PR. Deployment ([`deploy.yml`](.github/workflows/deploy.yml)) is
**manual only** (button or `release-*` tag) and rolls pinned images via
`docker-compose.prod.yml` behind Caddy.

## Security & compliance

| Guarantee | How it's enforced |
| --- | --- |
| API/UI separation | The web app calls only its same-origin proxy → FastAPI; it never imports `rag_core`. |
| Tenant isolation | Tenant id derives from auth, never the client; every collection, table, and cache key is tenant-scoped. |
| Clause provenance | Every `CriticalClause` carries `source_chunk_id` + `page_number`, reconciled from retrieval metadata. |
| Immutable audit trail | `activity_log` is append-only — no `UPDATE`/`DELETE` path exists (enforced by a static test). |
| Upload safety | Size cap, magic-byte MIME sniff, and page cap run **before** any parsing. |
| Secret handling | The backend API key never reaches the browser; the Next.js server-side proxy attaches it. |
| Env-gated provider | Production refuses to boot on a free-tier LLM provider. |
| Cost controls | Access-code gate + per-IP rate limits + provider spend cap. |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design and
[docs/SCALING_PATH.md](docs/SCALING_PATH.md) for the single-node → managed-infra
upgrade path.
