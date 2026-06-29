# Deployment & Engineering Hygiene

## What deploys where, and why

Kiwi Sorter has two server components with different deployment profiles:

| Component | Deploys to cloud? | Why |
|-----------|-------------------|-----|
| **Detection Platform** (:5000) | No — runs on the host | Needs the local webcam / XREAL Eye camera and serves the AR glasses on the LAN. Hardware-bound by design. |
| **DocMindAI RAG** (:5001) | **Yes** | Pure software (documents, vectors, LLM API, database). No hardware dependency → containerised and cloud-ready. |

So the "ships to production" story is **DocMindAI**: it's containerised, runs
behind gunicorn, and points at a managed PostgreSQL. The detection app stays
local because a cloud box has no camera — an honest architectural boundary, not a
gap.

## Run DocMindAI in Docker (local)

```bash
cd "DocMindAi Dai"
docker build -t docmindai .
docker run -p 5001:5001 --env-file .env docmindai
# → http://localhost:5001
```

The image pre-downloads the MiniLM embedding model at build time, so the first
request is fast and the container needs no internet at runtime (except for the
Claude and optional Brave APIs).

### Required environment (`.env` or platform secrets)

```
ANTHROPIC_API_KEY=sk-ant-...
PG_CONNECTION=postgresql://user:pass@host:5432/kiwi_sorter
BRAVE_API_KEY=            # optional (web search)
FLASK_SECRET_KEY=<random> # persists chat sessions
```

## Deploy to a cloud host (Railway / Render / Fly.io / Azure)

1. Provision a **managed PostgreSQL** instance; run `db_setup.sql` against it.
2. Point the service at this repo subdirectory (`DocMindAi Dai/`) — the platform
   builds the Dockerfile automatically.
3. Set the env vars above (use the managed DB's connection string).
4. Expose port 5001. Deploy.

FAISS persists to `faiss_index/` on the container's disk; for a stateless host,
attach a volume or re-run `/load-scans` / re-upload after a restart. (A future
hardening step is to migrate the vector store to **pgvector** so the database is
the single source of truth — the code is structured to allow this swap.)

## Tests

A unit suite covers the retrieval core (tokeniser, reciprocal-rank fusion, web
gating) — no DB or network needed:

```bash
"DocMindAi Dai/.venv/Scripts/python" -m pytest tests -q
```

```
6 passed
```

## Local everyday run (no Docker)

For development on the laptop, the control panel is simpler than Docker:

```
setup_admin.bat        # once: firewall + PostgreSQL
Kiwi Sorter.hta        # Start System
```

## Hardening backlog (future)

- Migrate FAISS → pgvector (single source of truth, no disk volume needed).
- Rate-limit `/ask` and `/upload`; add request-size and auth guards for a public deploy.
- CI: run `pytest` on push; build the Docker image as a check.
- Structured logging + a `/healthz` probe for the platform's health checks.
