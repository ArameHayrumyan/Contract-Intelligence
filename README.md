# Secure Contract Intelligence & SLA Auditor

A production-quality demo that scales into a real enterprise deployment without
architectural rework. It ingests contract PDFs (native text or OCR), produces a
structured, **provenance-backed** risk audit per contract, and answers
cross-document questions over a tenant's document set.

> Every cross-cutting seam — auth, multi-tenancy, LLM provider, background
> processing — is a real interface today, so the lightweight demo implementation
> can be swapped for an enterprise one by replacing one module. See
> [docs/SCALING_PATH.md](docs/SCALING_PATH.md).

## Monorepo layout

```
secure-contract-intelligence/
├── apps/
│   ├── api/      FastAPI — the ONLY consumer of rag_core
│   └── web/      Next.js (App Router) + TypeScript — calls the API only
├── packages/
│   └── rag_core/ all business logic (config, security, processor, storage,
│                 engine, ingestion_queue, schemas)
├── deploy/docker/  dev + prod compose, Caddy reverse proxy
├── tests/        pytest (processor, engine, API, config gate)
└── docs/         ARCHITECTURE.md, SCALING_PATH.md
```

## Quick start (local)

1. **Configure**
   ```bash
   cp .env.example .env
   # set GROQ_API_KEY (or another dev provider) in .env
   ```

2. **Run the stack**
   ```bash
   docker compose -f deploy/docker/docker-compose.yml up --build
   ```
   - Web UI: http://localhost:3000 (enter the `ACCESS_CODE` from `.env`)
   - API docs: http://localhost:8000/docs

### Run the backend without Docker

```bash
pip install -e "packages/rag_core[dev]"
pip install -r apps/api/requirements.txt
cd apps/api && uvicorn main:app --reload
```

### Run the frontend without Docker

```bash
cd apps/web && npm install && npm run dev
```

## Testing & quality

```bash
pip install -e "packages/rag_core[dev]"
ruff check packages/rag_core apps/api tests
mypy packages/rag_core/rag_core apps/api
pytest -q

cd apps/web && npm run lint && npm run typecheck
```

CI (`.github/workflows/ci.yml`) gates every PR with all of the above.
Deployment (`.github/workflows/deploy.yml`) is **manual only** — a button click
or a `release-*` tag — and SSHes into the DigitalOcean Droplet to roll the
pinned images via `docker-compose.prod.yml`.

## Key guarantees

| Constraint | How it's enforced |
| --- | --- |
| API/UI separation | The web app calls only its own same-origin proxy → FastAPI; it never imports `rag_core`. |
| Tenant-scoped data | Tenant id comes from auth, never the client; every Chroma collection is `tenant_{id}_contracts` and queries are tenant-filtered. |
| Clause provenance | Every `CriticalClause` carries `source_chunk_id` + `page_number`, reconciled from retrieval metadata. |
| Env-gated provider | Production refuses to boot on a free-tier LLM provider (`ConfigurationError`). |
| Cost-abuse controls | Access-code gate + per-IP `slowapi` limits on `/documents` and `/qa` + provider spend cap. |

## Security & cost notes

- Uploads are validated (size cap, magic-byte MIME sniff, page cap) **before**
  any parsing.
- The backend API key never reaches the browser — the Next.js server-side proxy
  attaches it.
- In production, set a hard monthly spend cap / billing alert in your paid LLM
  provider's dashboard (the GitHub Student Pack DigitalOcean credit does **not**
  cover third-party model costs — budget them separately).

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design.
