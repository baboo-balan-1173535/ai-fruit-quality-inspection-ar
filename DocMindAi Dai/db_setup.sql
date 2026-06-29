-- ============================================================
--  DocMind AI + Kiwi Sorter — PostgreSQL Setup Script
--  Run this once in pgAdmin 4 Query Tool
--
--  Steps:
--    1. Open pgAdmin 4
--    2. Right-click Databases → Create → Database → name it "kiwi_sorter"
--    3. Open Query Tool on kiwi_sorter
--    4. Paste this entire file and click Execute (F5)
-- ============================================================


-- ── 1. Extensions ────────────────────────────────────────────
-- uuid-ossp: fallback for gen_random_uuid() on older PostgreSQL builds
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- NOTE: pgvector extension (vector) is NOT required for this setup.
-- Vector embeddings are stored in a local FAISS index (faiss_index/ folder).
-- When pgvector becomes available for PostgreSQL 18, run:
--   CREATE EXTENSION IF NOT EXISTS vector;
-- and migrate using the pgvector version of rag_engine.py.


-- ── 2. Documents registry ────────────────────────────────────
-- Tracks every file that has been uploaded and indexed.
CREATE TABLE IF NOT EXISTS documents (
    id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    filename     TEXT         NOT NULL,
    file_hash    TEXT         UNIQUE NOT NULL,   -- SHA-256: prevents re-embedding same file
    source_type  TEXT         NOT NULL DEFAULT 'upload',  -- 'upload' | 'scan_report' | 'web'
    page_count   INTEGER      NOT NULL DEFAULT 0,
    file_size    INTEGER      NOT NULL DEFAULT 0,         -- bytes
    chunk_count  INTEGER      NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    metadata     JSONB        NOT NULL DEFAULT '{}'
);


-- ── 3. Chat message history ──────────────────────────────────
-- Stores the full conversation per browser session.
-- Used to pass conversation context to Claude (multi-turn chat).
CREATE TABLE IF NOT EXISTS chat_messages (
    id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   TEXT         NOT NULL,          -- browser session UUID (from Flask session / localStorage)
    role         TEXT         NOT NULL CHECK (role IN ('user', 'assistant')),
    content      TEXT         NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_session
    ON chat_messages (session_id, created_at DESC);


-- ── 4. Kiwi Sorter scan reports ──────────────────────────────
-- Persists every fruit scan from the kiwi sorter system.
-- Will be ingested into the RAG vector store so the chatbot can answer
-- questions like "how many Grade A kiwis did I scan last Tuesday?"
CREATE TABLE IF NOT EXISTS scan_reports (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    TEXT,
    scan_image    TEXT,                          -- path to saved annotated image
    fruit_count   INTEGER      NOT NULL DEFAULT 0,
    results       JSONB        NOT NULL DEFAULT '[]',   -- full per-fruit Claude analysis array
    mode          TEXT         NOT NULL DEFAULT 'legacy',  -- 'legacy' | 'ar'
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scan_session
    ON scan_reports (session_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_scan_created
    ON scan_reports (created_at DESC);


-- ── 5. Verify setup ──────────────────────────────────────────
SELECT table_name
FROM   information_schema.tables
WHERE  table_schema = 'public'
ORDER  BY table_name;

-- Should return: chat_messages, documents, scan_reports
