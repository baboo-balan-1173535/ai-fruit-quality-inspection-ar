# DocMindAI — RAG Reporting & Chat Layer

The document-intelligence component of [Kiwi Sorter](../README.md). Ingests
inspection reports and documents, then answers grounded questions about them with
**inline citations**, **hybrid retrieval**, and **SQL-exact analytics**.

Flask · port **5001** · Python 3.11

## Features

- **Document ingestion** — PDF / DOCX / TXT, SHA-256 dedupe, chunked, embedded
  (`all-MiniLM-L6-v2`) into FAISS; metadata + cached summary in PostgreSQL.
- **Hybrid retrieval** — dense semantic (FAISS) + sparse keyword (BM25 over the
  whole corpus), combined with **reciprocal-rank fusion**.
- **Grounded answers with citations** — Claude (`claude-haiku-4-5`, prompt-cached)
  cites numbered passages inline as `[1]`,`[2]`…; clicking a citation flashes its
  source card.
- **Multi-source routing** — uploaded documents · live fruit-scan database
  (exact SQL counts) · optional Brave web search; the assistant picks the right
  source per question and keeps context across follow-ups.
- **Document Library** — list, scope retrieval to selected docs, and delete
  (vectors removed from FAISS).
- **Analytics** — SQL-aggregated scan dashboard; counting questions answered from
  exact totals, not a retrieved sample.
- **Streaming** — token-by-token over Server-Sent Events, with a fallback.

## Architecture

```
upload ─▶ chunk ─▶ embed ─▶ FAISS (on disk)        documents/chat_messages/scan_reports
                                   │                          │ (PostgreSQL)
question ─▶ FAISS + BM25 ─▶ RRF fusion ─▶ numbered passages ─▶ Claude ─▶ cited answer
            └─ scan-intent? ─▶ exact SQL counts ──────────────┘
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Create `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
PG_CONNECTION=postgresql://postgres:<pw>@localhost:5432/kiwi_sorter
BRAVE_API_KEY=            # optional — enables web search
FLASK_SECRET_KEY=         # optional — persists sessions across restarts
```

Run: `.venv\Scripts\python app.py` → http://localhost:5001

## Key endpoints

| Route | Purpose |
|-------|---------|
| `POST /upload` | Index a document |
| `POST /ask` · `POST /ask-stream` | Ask (non-streaming / streaming) |
| `GET /documents` · `POST /documents/<id>/delete` | Library |
| `POST /load-scans` | Ingest scan reports |
| `GET /analytics` | SQL scan statistics |
| `GET /status` | DB / web / readiness health |

## Tests

```bash
.venv\Scripts\python -m pytest ../tests -q
```
