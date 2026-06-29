# rag_engine.py — DocMind AI v2.1
# Vector store: FAISS saved to disk (faiss_index/ folder) — survives restarts.
# All other data (chat history, document registry, scan reports) stays in PostgreSQL.
# To switch to pgvector later: replace the FAISS section with the pgvector version.

import os
import json
import time
import hashlib
import requests
from pathlib import Path

import numpy as np
import anthropic
import psycopg2
from psycopg2.extras import RealDictCursor
from langchain_core.documents import Document
from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader, TextLoader
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from rank_bm25 import BM25Okapi


# ── Config ─────────────────────────────────────────────────────────────────────
BRAVE_API_KEY  = os.environ.get("BRAVE_API_KEY", "")
PG_CONNECTION  = os.environ.get(
    "PG_CONNECTION",
    "postgresql://postgres:password@localhost:5432/kiwi_sorter"
)
EMBED_MODEL    = "sentence-transformers/all-MiniLM-L6-v2"
FAISS_DIR      = Path(__file__).parent / "faiss_index"   # persistent index folder
HISTORY_LIMIT  = 10   # last N message pairs passed to Claude as context


# ── Web search ─────────────────────────────────────────────────────────────────
def brave_search(query: str, count: int = 5) -> tuple[list[dict], bool]:
    """
    Returns (results, web_available).
    web_available=False ONLY when BRAVE_API_KEY is missing (config issue).
    Transient errors return ([], True) so the frontend shows a different message.
    """
    if not BRAVE_API_KEY:
        return [], False
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": BRAVE_API_KEY,
            },
            params={"q": query, "count": count},
            timeout=10,
        )
        resp.raise_for_status()
        raw = resp.json().get("web", {}).get("results", [])
        print(f"[WEB] {len(raw)} results for '{query}'")
        return [
            {
                "title":       r.get("title", ""),
                "url":         r.get("url", ""),
                "description": r.get("description", ""),
            }
            for r in raw
        ], True
    except Exception as e:
        print(f"[WEB ERROR] {e}")
        return [], True   # key is present, search failed transiently


# ── RAG Engine ─────────────────────────────────────────────────────────────────
class RAGEngine:

    def __init__(self):
        self.current_doc_id: str | None  = None
        self.current_doc_chunks: list    = []   # in-memory for BM25
        self.faiss_db                    = None  # FAISS vector store
        self._bm25       = None   # BM25 keyword index over the WHOLE corpus
        self._bm25_docs  = []     # corpus aligned with self._bm25
        self._scans_cache = None  # cached "scan_reports has rows?" (TTL below)
        self._scans_ts    = 0.0

        self.client     = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self.embeddings = self._load_embeddings()

        # Load existing FAISS index from disk if present
        self._load_faiss()

        # Summary cache column (idempotent) — re-uploading an already-indexed
        # document must not pay for a fresh Claude summarisation.
        try:
            with self._pg() as conn:
                with conn.cursor() as cur:
                    cur.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary TEXT")
        except Exception as e:
            print(f"[DB] summary column check failed (DB offline?): {e}")

    # ── Setup ───────────────────────────────────────────────────────────────────

    def _load_embeddings(self) -> HuggingFaceEmbeddings:
        print("[RAG] Loading embedding model…")
        emb = HuggingFaceEmbeddings(
            model_name=EMBED_MODEL,
            model_kwargs={"device": "cpu"},
        )
        print("[RAG] Embedding model ready.")
        return emb

    def _load_faiss(self):
        """Load FAISS index from disk if it exists (persists between restarts)."""
        if FAISS_DIR.exists() and (FAISS_DIR / "index.faiss").exists():
            try:
                self.faiss_db = FAISS.load_local(
                    str(FAISS_DIR),
                    self.embeddings,
                    allow_dangerous_deserialization=True,
                )
                print(f"[RAG] FAISS index loaded from {FAISS_DIR}")
                self._invalidate_bm25()
            except Exception as e:
                print(f"[RAG] Could not load FAISS index: {e} — will rebuild on next upload.")
                self.faiss_db = None
        else:
            print("[RAG] No existing FAISS index — will create on first upload.")

    def _save_faiss(self):
        """Save FAISS index to disk so it survives restarts."""
        if self.faiss_db:
            FAISS_DIR.mkdir(parents=True, exist_ok=True)
            self.faiss_db.save_local(str(FAISS_DIR))
            print(f"[RAG] FAISS index saved to {FAISS_DIR}")
        # Corpus changed (add/delete/scan reload) — BM25 rebuilds on next ask
        self._invalidate_bm25()

    # ── PostgreSQL helpers ──────────────────────────────────────────────────────

    def _pg(self):
        """Open a psycopg2 connection."""
        return psycopg2.connect(PG_CONNECTION)

    def _file_hash(self, filepath: str) -> str:
        """SHA-256 of file — used to skip re-indexing unchanged files."""
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                h.update(block)
        return h.hexdigest()

    def _register_document(
        self, filename: str, fhash: str, pages: int, size: int, n_chunks: int
    ) -> str:
        """Upsert document record, return its UUID string."""
        with self._pg() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO documents (filename, file_hash, page_count, file_size, chunk_count)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (file_hash) DO UPDATE
                        SET filename    = EXCLUDED.filename,
                            chunk_count = EXCLUDED.chunk_count
                    RETURNING id
                    """,
                    (filename, fhash, pages, size, n_chunks),
                )
                return str(cur.fetchone()[0])

    def _get_summary(self, doc_id: str) -> str | None:
        try:
            with self._pg() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT summary FROM documents WHERE id = %s::uuid", (doc_id,))
                    row = cur.fetchone()
                    return row[0] if row and row[0] else None
        except Exception:
            return None

    def _store_summary(self, doc_id: str, summary: str):
        try:
            with self._pg() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE documents SET summary = %s WHERE id = %s::uuid",
                                (summary, doc_id))
        except Exception as e:
            print(f"[DB] store_summary error: {e}")

    def _document_exists(self, fhash: str) -> str | None:
        """Return document UUID if already registered, else None."""
        try:
            with self._pg() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM documents WHERE file_hash = %s", (fhash,)
                    )
                    row = cur.fetchone()
                    return str(row[0]) if row else None
        except Exception:
            return None

    # ── Document library ────────────────────────────────────────────────────────

    def list_documents(self) -> list[dict]:
        """All registered documents, newest first (for the Library panel)."""
        try:
            with self._pg() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT id::text, filename, page_count, file_size,
                               chunk_count, created_at::text,
                               (summary IS NOT NULL) AS has_summary
                        FROM   documents
                        ORDER  BY created_at DESC
                        """
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            print(f"[DB] list_documents error: {e}")
            return []

    def _delete_from_faiss(self, match) -> int:
        """
        Remove every vector whose chunk metadata satisfies `match(metadata)`.
        Uses LangChain-FAISS delete-by-docstore-id — no re-embedding needed.
        Returns the number of chunks removed.
        """
        if self.faiss_db is None:
            return 0
        ids = [
            ds_id
            for ds_id, doc in self.faiss_db.docstore._dict.items()
            if match(doc.metadata or {})
        ]
        if ids:
            self.faiss_db.delete(ids)
            self._save_faiss()
        return len(ids)

    def delete_document(self, doc_id: str) -> dict:
        """Remove a document everywhere: FAISS vectors + PostgreSQL registry."""
        removed = self._delete_from_faiss(lambda md: md.get("doc_id") == doc_id)
        try:
            with self._pg() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM documents WHERE id = %s::uuid", (doc_id,))
        except Exception as e:
            print(f"[DB] delete_document error: {e}")
        if self.current_doc_id == doc_id:
            self.current_doc_id     = None
            self.current_doc_chunks = []
        print(f"[RAG] Document {doc_id} deleted ({removed} chunks removed from FAISS).")
        return {"deleted": True, "chunks_removed": removed}

    # ── Chat history ────────────────────────────────────────────────────────────

    def save_message(self, session_id: str, role: str, content: str):
        try:
            with self._pg() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO chat_messages (session_id, role, content) VALUES (%s,%s,%s)",
                        (session_id, role, content),
                    )
        except Exception as e:
            print(f"[DB] save_message error: {e}")

    def load_history(self, session_id: str, limit: int = HISTORY_LIMIT) -> list[dict]:
        """Return last `limit` messages in chronological order for Claude context."""
        try:
            with self._pg() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT role, content
                        FROM   chat_messages
                        WHERE  session_id = %s
                        ORDER  BY created_at DESC
                        LIMIT  %s
                        """,
                        (session_id, limit),
                    )
                    rows = cur.fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        except Exception as e:
            print(f"[DB] load_history error: {e}")
            return []

    def clear_history(self, session_id: str):
        try:
            with self._pg() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM chat_messages WHERE session_id = %s", (session_id,)
                    )
        except Exception as e:
            print(f"[DB] clear_history error: {e}")

    # ── Document ingestion ──────────────────────────────────────────────────────

    def load_and_index(self, filepath: str, filename: str) -> dict:
        """
        Load, split, embed, store. If file hash already exists in PostgreSQL
        and FAISS index is loaded, skips re-embedding (fast reload).
        """
        ext = filepath.rsplit(".", 1)[-1].lower()
        if ext not in ("pdf", "docx", "txt"):
            raise ValueError(f"Unsupported file type: .{ext}  (use PDF, DOCX or TXT)")

        # Load raw pages
        if ext == "pdf":
            loader = PyPDFLoader(filepath)
        elif ext == "docx":
            loader = Docx2txtLoader(filepath)
        else:
            loader = TextLoader(filepath, encoding="utf-8")

        all_docs = loader.load()
        fhash    = self._file_hash(filepath)
        fsize    = os.path.getsize(filepath)

        # If already registered AND FAISS index is on disk, skip re-embedding
        existing_id = self._document_exists(fhash)
        if existing_id and self.faiss_db is not None:
            print(f"[RAG] Already indexed (id={existing_id}) — loading from DB + FAISS.")
            self.current_doc_id     = existing_id
            self.current_doc_chunks = self._rebuild_chunks(all_docs)
            # Cached summary — only call Claude if this doc never got one stored
            summary = self._get_summary(existing_id)
            if not summary:
                summary = self._summarise(all_docs)
                self._store_summary(existing_id, summary)
            return {
                "summary":         summary,
                "doc_id":          existing_id,
                "pages":           len(all_docs),
                "chunks":          len(self.current_doc_chunks),
                "filename":        filename,
                "already_indexed": True,
            }

        # Split into chunks
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=600,
            chunk_overlap=100,
            separators=["\n\n", "\n", ".", " ", ""],
        )
        chunks = splitter.split_documents(all_docs)
        print(f"[RAG] {len(all_docs)} page(s) → {len(chunks)} chunks")

        # Register in PostgreSQL
        doc_id = self._register_document(filename, fhash, len(all_docs), fsize, len(chunks))

        # Tag chunks with doc metadata
        for i, chunk in enumerate(chunks):
            chunk.metadata["doc_id"]      = doc_id
            chunk.metadata["chunk_index"] = i
            chunk.metadata["filename"]    = filename

        # Add to FAISS (merge into existing index or create new)
        if self.faiss_db is None:
            self.faiss_db = FAISS.from_documents(chunks, self.embeddings)
        else:
            self.faiss_db.add_documents(chunks)

        self._save_faiss()   # persist to disk immediately
        print(f"[RAG] FAISS index updated and saved.")

        self.current_doc_id     = doc_id
        self.current_doc_chunks = chunks

        summary = self._summarise(all_docs)
        self._store_summary(doc_id, summary)
        return {
            "summary":         summary,
            "doc_id":          doc_id,
            "pages":           len(all_docs),
            "chunks":          len(chunks),
            "filename":        filename,
            "already_indexed": False,
        }

    def _rebuild_chunks(self, all_docs) -> list:
        """Re-split loaded pages to get in-memory chunks for BM25 (no re-embedding)."""
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=600, chunk_overlap=100,
            separators=["\n\n", "\n", ".", " ", ""],
        )
        return splitter.split_documents(all_docs)

    def _summarise(self, all_docs) -> str:
        text = " ".join(d.page_content for d in all_docs)
        text = " ".join(text.split())[:3000]
        system = "You are a document analyst. Write clear, structured summaries using markdown."
        prompt = (
            "Write a structured summary (5–8 sentences) covering:\n"
            "- What the document is about\n"
            "- Its purpose and scope\n"
            "- The main topics or sections\n\n"
            f"Document:\n{text}\n\nSummary:"
        )
        return self._call_claude(
            messages=[{"role": "user", "content": prompt}],
            system=system,
            max_tokens=400,
        )

    # ── Keyword search (real BM25 over the WHOLE corpus) ───────────────────────
    # The old version was a hand-rolled TF-IDF over only the latest document's
    # in-memory chunks. This indexes every chunk in the FAISS docstore, so
    # keyword search covers all documents + scan history, and survives restarts.

    def _invalidate_bm25(self):
        self._bm25, self._bm25_docs = None, []

    @staticmethod
    def _tok(text: str) -> list[str]:
        return [w for w in text.lower().split() if len(w) > 2]

    def _ensure_bm25(self):
        if self._bm25 is not None or self.faiss_db is None:
            return
        self._bm25_docs = list(self.faiss_db.docstore._dict.values())
        corpus = [self._tok(d.page_content) for d in self._bm25_docs]
        if corpus:
            self._bm25 = BM25Okapi(corpus)
            print(f"[RAG] BM25 index built over {len(corpus)} chunks.")

    def _bm25_all(self, query: str, top_n: int = 8, scope: set | None = None) -> list:
        """Top keyword matches across the whole corpus (optionally doc-scoped)."""
        self._ensure_bm25()
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(self._tok(query))
        ranked = sorted(zip(scores, range(len(self._bm25_docs))),
                        key=lambda x: x[0], reverse=True)
        out = []
        for score, idx in ranked:
            if score <= 0 or len(out) >= top_n:
                break
            doc = self._bm25_docs[idx]
            if scope and (doc.metadata or {}).get("doc_id") not in scope:
                continue
            out.append(doc)
        return out

    @staticmethod
    def _rrf_fuse(result_lists: list[list], k: int = 60, top_n: int = 5) -> list:
        """
        Reciprocal-rank fusion: each list votes 1/(k+rank) per chunk. Robustly
        combines semantic + keyword rankings without score calibration.
        """
        scores, registry = {}, {}
        for results in result_lists:
            for rank, doc in enumerate(results):
                key = ((doc.metadata or {}).get("doc_id", ""),
                       (doc.metadata or {}).get("chunk_index", id(doc)))
                scores[key]   = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
                registry[key] = doc
        best = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [registry[key] for key, _ in best]

    # ── Claude ──────────────────────────────────────────────────────────────────

    def _call_claude(self, messages: list, system: str, max_tokens: int = 700) -> str:
        """
        Call Claude Haiku with full message history.
        System prompt is marked for prompt caching — cuts cost on repeated queries.
        """
        response = self.client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
        )
        return response.content[0].text.strip()

    # ── Main ask ────────────────────────────────────────────────────────────────

    def _prepare(self, question: str, use_web: bool = False,
                 doc_ids: list | None = None, history: list | None = None):
        """
        Shared retrieval + prompt assembly for ask() and ask_stream().
        `history` (recent messages) lets scan-intent detection persist across
        follow-up questions. Returns (system, user_content, sources, web_available).
        """
        recent_text = " ".join(
            m.get("content", "") for m in (history or [])[-4:])

        # 1 — Retrieval. DAi has three possible knowledge sources and decides
        #     which to surface from the question itself: uploaded documents
        #     (FAISS), the user's fruit-scan database (PostgreSQL), and the web.
        relevant_docs = []
        scope = set(doc_ids) if doc_ids else None

        # A) Uploaded-document / corpus retrieval (semantic + BM25, fused)
        if self.faiss_db:
            try:
                if scope:
                    semantic_docs = self.faiss_db.similarity_search(
                        question, k=8, fetch_k=60,
                        filter=lambda md: md.get("doc_id") in scope)
                else:
                    semantic_docs = self.faiss_db.similarity_search(question, k=8)
            except Exception as e:
                print(f"[RAG] FAISS search error: {e}")
                semantic_docs = []
            kw_docs       = self._bm25_all(question, top_n=8, scope=scope)
            relevant_docs = self._rrf_fuse([semantic_docs, kw_docs], top_n=5)

        # B) Live scan database (exact SQL counts) on any fruit/scan question —
        #    works even with no document uploaded and no scans manually indexed.
        if not scope:   # a Library scope means "only these documents"
            scan_doc = self._scan_context_doc(question, recent_text)
            if scan_doc is not None:
                relevant_docs = relevant_docs + [scan_doc]

        # Classify what each passage is, for honest prompt framing + numbering
        def _is_scan(d):
            md = d.metadata or {}
            return md.get("source") == "scan_history" or md.get("doc_id") == "live_stats"

        has_docs = any(not _is_scan(d) for d in relevant_docs)
        has_scan = any(_is_scan(d)     for d in relevant_docs)

        blocks = []
        for i, d in enumerate(relevant_docs, 1):
            fname = d.metadata.get("filename", "document")
            page  = d.metadata.get("page", d.metadata.get("page_number"))
            try:    page_label = f", p.{int(page) + 1}"
            except Exception: page_label = ""
            body = " ".join(d.page_content.split())[:800]
            blocks.append(f"[{i}] ({fname}{page_label})\n{body}")
        doc_context = "\n\n".join(blocks)[:4500]

        # C) Web search
        web_results, web_available = [], bool(BRAVE_API_KEY)
        web_context = ""
        if use_web:
            web_results, web_available = brave_search(question)
            base = len(relevant_docs)   # web passages continue the numbering
            snippets = [
                f"[{base + j}] (web: {r['title']})\nURL: {r['url']}\n{r['description']}"
                for j, r in enumerate(
                    [r for r in web_results if r.get("description")], 1)
            ]
            web_context = "\n\n".join(snippets)

        # 2 — System prompt: name the sources actually present so the model
        #     routes the question to the right one instead of refusing.
        sources_present = []
        if has_docs:     sources_present.append("excerpts from uploaded documents")
        if has_scan:     sources_present.append("the user's fruit-scan database (exact live counts)")
        if web_context:  sources_present.append("web search results")

        CITE_RULES = (
            " Cite sources inline as [1], [2], … matching the numbered passages, "
            "right after the claim each supports. Cite only numbers that exist. "
            "If none of the provided sources answer the question, say so plainly."
        )
        if sources_present:
            system = (
                "You are DAi, a document and data assistant. For this question you "
                "have access to: " + "; ".join(sources_present) + ". "
                "Use whichever source(s) are relevant — a question about scanned "
                "fruit should be answered from the fruit-scan database, a question "
                "about a document from its excerpts. Use markdown formatting."
                + CITE_RULES
            )
        else:
            system = "You are DAi, a helpful assistant. Use markdown in your answers."

        # 3 — User message: one combined numbered-source block
        all_context = doc_context
        if web_context:
            all_context = (all_context + "\n\n" + web_context) if all_context else web_context
        if all_context:
            user_content = f"Numbered sources:\n\n{all_context}\n\nQuestion: {question}"
        else:
            user_content = question

        # 5 — Build sources for frontend (n matches the inline [n] citations)
        sources = []
        for i, doc in enumerate(relevant_docs, 1):
            meta     = doc.metadata
            page_raw = meta.get("page", meta.get("page_number"))
            try:    page_num = int(page_raw) + 1
            except: page_num = page_raw
            sources.append({
                "n":      i,
                "type":   "document",
                "text":   doc.page_content,
                "source": meta.get("filename", os.path.basename(meta.get("source", "doc"))),
                "page":   page_num,
            })
        base = len(relevant_docs)
        for j, r in enumerate(
                [r for r in web_results if r.get("description")], 1):
            if r.get("url"):
                sources.append({
                    "n":      base + j,
                    "type":   "web",
                    "text":   r.get("description", ""),
                    "source": r.get("title", ""),
                    "url":    r.get("url", ""),
                    "page":   None,
                })

        return system, user_content, sources, web_available

    def ask(self, question: str, session_id: str, use_web: bool = False,
            doc_ids: list | None = None) -> dict:
        """Non-streaming answer (kept as the fallback path)."""
        history  = self.load_history(session_id)
        system, user_content, sources, web_available = \
            self._prepare(question, use_web, doc_ids, history)

        messages = history + [{"role": "user", "content": user_content}]

        self.save_message(session_id, "user", question)   # plain question, not padded
        answer = self._call_claude(messages, system)
        self.save_message(session_id, "assistant", answer)

        return {
            "answer":        answer,
            "sources":       sources,
            "web_available": web_available,
            "doc_available": bool(self.current_doc_id),
        }

    def ask_stream(self, question: str, session_id: str, use_web: bool = False,
                   doc_ids: list | None = None):
        """
        Streaming version: yields ("delta", text) chunks as Claude writes,
        then one final ("done", {sources, ...}) event. History is saved the
        same way as ask().
        """
        history  = self.load_history(session_id)
        system, user_content, sources, web_available = \
            self._prepare(question, use_web, doc_ids, history)

        messages = history + [{"role": "user", "content": user_content}]
        self.save_message(session_id, "user", question)

        parts = []
        with self.client.messages.stream(
            model="claude-haiku-4-5",
            max_tokens=700,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                parts.append(text)
                yield "delta", text

        answer = "".join(parts).strip()
        self.save_message(session_id, "assistant", answer)
        yield "done", {
            "sources":       sources,
            "web_available": web_available,
            "doc_available": bool(self.current_doc_id),
        }

    # ── Scan report integration ─────────────────────────────────────────────────

    def _scan_report_to_text(self, report: dict) -> str:
        """
        Convert one scan_reports row into a readable paragraph suitable for
        embedding. Structured enough for keyword search; natural enough for
        semantic similarity.
        """
        SORT_LABELS = {"1": "Use Immediately", "2": "Use Soon",
                       "3": "Store",           "4": "Discard"}

        created = str(report.get("created_at", "Unknown time"))[:19]
        mode    = report.get("mode", "legacy").replace("_", " ").title()
        results = report.get("results", [])
        if isinstance(results, str):
            results = json.loads(results)

        lines = [
            "Fruit Scan Report",
            f"Date: {created}",
            f"Mode: {mode} Camera",
            f"Total Fruits Detected: {len(results)}",
            "",
        ]
        for i, f in enumerate(results, 1):
            sort_text = SORT_LABELS.get(str(f.get("sort", "")), str(f.get("sort", "?")))
            lines += [
                f"Fruit {i}: {f.get('fruit', 'Unknown').upper()}",
                f"  Quality:       {f.get('quality', '?')}",
                f"  Decay Stage:   {f.get('decay_stage', '?')}",
                f"  Days Remaining:{f.get('days', '?')}",
                f"  Sort Action:   {sort_text}",
                f"  Size:          {f.get('size', '?')}",
                f"  Dimensions:    {f.get('width_cm','?')} cm × {f.get('height_cm','?')} cm",
                f"  Area:          {f.get('area_cm2','?')} cm²",
            ]
            if f.get("defects") and f.get("defects") != "None":
                lines.append(f"  Defects:       {f.get('defects')}")
            if f.get("recommendation"):
                lines.append(f"  Recommendation:{f.get('recommendation')}")
            if f.get("color_desc"):
                lines.append(f"  Colour:        {f.get('color_desc')}")
            lines.append("")

        return "\n".join(lines)

    def _scan_stats(self, rows: list) -> dict:
        """Aggregate quality and fruit-type counts across all loaded scans."""
        by_quality, by_type = {}, {}
        total_fruits = 0
        for row in rows:
            results = row.get("results", [])
            if isinstance(results, str):
                results = json.loads(results)
            for f in results:
                total_fruits += 1
                q  = f.get("quality", "Unknown")
                ft = f.get("fruit",   "Unknown").lower()
                by_quality[q]  = by_quality.get(q,  0) + 1
                by_type[ft]    = by_type.get(ft, 0) + 1
        return {
            "total_scans":  len(rows),
            "total_fruits": total_fruits,
            "by_quality":   by_quality,
            "by_type":      by_type,
        }

    def _stats_to_markdown(self, stats: dict) -> str:
        """Format scan stats as a markdown summary for the DocMindAI UI."""
        lines = [
            f"**{stats['total_scans']} scan(s) loaded** — "
            f"**{stats['total_fruits']} fruits** total\n",
        ]
        if stats["by_type"]:
            by_type = ", ".join(
                f"{k.title()}: **{v}**"
                for k, v in sorted(stats["by_type"].items(), key=lambda x: -x[1])
            )
            lines.append(f"**By fruit type:** {by_type}\n")
        if stats["by_quality"]:
            order = ["Good", "Acceptable", "Poor", "Bad", "Error", "Unknown"]
            sorted_q = sorted(
                stats["by_quality"].items(),
                key=lambda x: order.index(x[0]) if x[0] in order else 99
            )
            by_q = ", ".join(f"{k}: **{v}**" for k, v in sorted_q)
            lines.append(f"**By quality:** {by_q}\n")
        lines.append("*Ask me anything — e.g. 'How many good apples did I scan?' "
                     "or 'Which fruits need immediate use?'*")
        return "\n".join(lines)

    def load_scan_reports(self, limit: int = 500) -> dict:
        """
        Pull scan_reports from PostgreSQL, convert to text Documents,
        add to FAISS index, and return stats for the UI.

        Each scan becomes one Document — the chatbot can then answer
        questions like "how many grade-A apples did I scan today?" or
        "which batch had the most defects?"
        """
        try:
            with self._pg() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT id::text, session_id, scan_image,
                               fruit_count, results, mode,
                               created_at::text
                        FROM   scan_reports
                        ORDER  BY created_at DESC
                        LIMIT  %s
                        """,
                        (limit,),
                    )
                    rows = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            raise RuntimeError(f"Could not read scan reports from database: {e}")

        if not rows:
            return {
                "loaded":       0,
                "total_fruits": 0,
                "summary":      "No scan reports found in the database yet.\n\n"
                                "Run a fruit scan in the Kiwi Sorter app first, "
                                "then reload scan history here.",
            }

        # Convert each row to a LangChain Document
        docs = []
        for i, row in enumerate(rows):
            text = self._scan_report_to_text(row)
            docs.append(Document(
                page_content=text,
                metadata={
                    "doc_id":      f"scan_{row['id']}",
                    "chunk_index": i,
                    "filename":    "Scan History",
                    "source":      "scan_history",
                    "scan_id":     row["id"],
                    "created_at":  row.get("created_at", ""),
                },
            ))

        # Replace any previously loaded scan docs FIRST — reloading scan history
        # used to ADD duplicates to the index on every click.
        stale = self._delete_from_faiss(lambda md: md.get("source") == "scan_history")
        if stale:
            print(f"[RAG] Removed {stale} stale scan documents before reload.")

        # Merge into FAISS (keeps any uploaded documents too)
        if self.faiss_db is None:
            self.faiss_db = FAISS.from_documents(docs, self.embeddings)
        else:
            self.faiss_db.add_documents(docs)
        self._save_faiss()

        # Mark engine as ready so the chatbot accepts questions
        self.current_doc_id     = "scan_history"
        self.current_doc_chunks = docs   # for BM25 keyword search

        stats   = self._scan_stats(rows)
        summary = self._stats_to_markdown(stats)
        print(f"[RAG] {len(docs)} scan reports loaded into FAISS.")

        return {
            "loaded":       len(docs),
            "total_fruits": stats["total_fruits"],
            "by_quality":   stats["by_quality"],
            "by_type":      stats["by_type"],
            "summary":      summary,
        }

    # ── Analytics (SQL truth, not retrieval) ────────────────────────────────────

    SORT_LABELS = {"1": "Use Immediately", "2": "Use Soon", "3": "Store", "4": "Discard"}

    def get_analytics(self) -> dict:
        """
        Aggregate ALL scan_reports directly from PostgreSQL. Counting questions
        answered by retrieval are wrong at scale (top-5 passages only) — this is
        the ground truth the Analytics panel (and stats chat context) uses.
        """
        with self._pg() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id::text, mode, fruit_count, results,
                           created_at::date::text AS day,
                           to_char(created_at, 'YYYY-MM-DD HH24:MI') AS ts
                    FROM   scan_reports
                    ORDER  BY created_at DESC
                    """
                )
                rows = [dict(r) for r in cur.fetchall()]

        by_quality, by_type, by_sort, by_day = {}, {}, {}, {}
        by_type_quality = {}   # cross-tab: fruit type → {quality → count}
        total_fruits = 0
        for row in rows:
            results = row.get("results") or []
            if isinstance(results, str):
                results = json.loads(results)
            day = row["day"]
            d = by_day.setdefault(day, {"scans": 0, "fruits": 0})
            d["scans"] += 1
            for f in results:
                total_fruits += 1
                d["fruits"]  += 1
                q  = f.get("quality", "Unknown")
                ft = (f.get("fruit") or "Unknown").lower()
                st = self.SORT_LABELS.get(str(f.get("sort", "")), "Unknown")
                by_quality[q] = by_quality.get(q, 0) + 1
                by_type[ft]   = by_type.get(ft, 0) + 1
                by_sort[st]   = by_sort.get(st, 0) + 1
                tq = by_type_quality.setdefault(ft, {})
                tq[q] = tq.get(q, 0) + 1

        recent = []
        for row in rows[:6]:
            results = row.get("results") or []
            if isinstance(results, str):
                results = json.loads(results)
            recent.append({
                "ts":     row["ts"],
                "mode":   row.get("mode", "legacy"),
                "fruits": len(results),
                "labels": [f"{(f.get('fruit') or '?')}/{f.get('quality','?')}"
                           for f in results[:4]],
            })

        return {
            "total_scans":     len(rows),
            "total_fruits":    total_fruits,
            "by_quality":      by_quality,
            "by_type":         by_type,
            "by_sort":         by_sort,
            "by_type_quality": by_type_quality,
            "by_day":          [{"day": k, **v} for k, v in sorted(by_day.items())][-14:],
            "recent":          recent,
        }

    # Fruit / scan vocabulary — if any of these appear, the question is about
    # the user's scans, so the live scan database is pulled in as a source.
    # Deliberately specific (fruit names, scan/quality terms) to avoid firing
    # on generic words like "good" in a document question.
    _SCAN_WORDS = (
        "apple", "banana", "orange", "kiwi", "mango", "pear", "grape",
        "fruit", "scan", "ripe", "ripeness", "decay", "decaying", "defect",
        "rotten", "spoil", "shelf life", "shelf-life", "discard", "grade",
        "freshness", "produce", "batch", "inspection",
    )

    # Quality / decay grade words — these continue a scan conversation even
    # without a fruit word ("how many are acceptable", "what about the poor ones").
    _GRADE_WORDS = ("acceptable", "good", "poor", "bad", "fresh", "overripe",
                    "ripe", "decaying", "rotten", "spoiled", "unknown")

    def _scan_intent(self, question: str, recent: str = "") -> bool:
        ql = question.lower()
        if any(w in ql for w in self._SCAN_WORDS):
            return True
        # Follow-up in an ongoing scan conversation: the current question is a
        # count/grade question with no explicit fruit word, but the recent chat
        # was clearly about scans. Keeps context across "how many are acceptable".
        recent_about_scans = any(w in (recent or "").lower() for w in self._SCAN_WORDS)
        if recent_about_scans and (any(w in ql for w in self._AGG_WORDS)
                                   or any(w in ql for w in self._GRADE_WORDS)):
            return True
        return False

    _AGG_WORDS = ("how many", "count", "total", "average", "percent", "most",
                  "least", "how much", "number of", "distribution", "summary",
                  "overall", "statistics", "stats", "what about", "which")

    def _scan_context_doc(self, question: str, recent: str = ""):
        """
        On a fruit/scan question, return a live scan-database passage computed
        by SQL — exact counts (incl. quality-by-fruit-type cross-tab) straight
        from PostgreSQL. Independent of whether scans were manually indexed, so
        the chatbot can always answer scan questions.
        """
        if not self._scan_intent(question, recent):
            return None
        try:
            a = self.get_analytics()
        except Exception:
            return None
        if not a["total_scans"]:
            return None

        cross = "; ".join(
            f"{t}: " + ", ".join(f"{q} {n}" for q, n in qd.items())
            for t, qd in sorted(a["by_type_quality"].items())
        )
        text = (
            "Live fruit-scan database (exact counts from ALL scans, not a sample):\n"
            f"- Totals: {a['total_scans']} scans, {a['total_fruits']} fruits.\n"
            f"- By quality: {a['by_quality']}.\n"
            f"- By fruit type: {a['by_type']}.\n"
            f"- Quality broken down per fruit type: {cross}.\n"
            f"- By recommended action: {a['by_sort']}.\n"
            "Use these exact numbers for any counting question about scanned fruit "
            "(e.g. 'good apples' = the Good count under 'apple' above)."
        )
        return Document(page_content=text,
                        metadata={"filename": "Live scan database",
                                  "doc_id": "live_stats", "chunk_index": -1,
                                  "source": "scan_history"})

    def _scans_exist(self) -> bool:
        """Cheap, cached check: does the scan_reports table have any rows?"""
        now = time.time()
        if self._scans_cache is not None and now - self._scans_ts < 15:
            return self._scans_cache
        exists = False
        try:
            with self._pg() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM scan_reports LIMIT 1")
                    exists = cur.fetchone() is not None
        except Exception:
            exists = False
        self._scans_cache, self._scans_ts = exists, now
        return exists

    # ── Status ──────────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        # Ready when ANY knowledge source exists: a doc loaded this run, a FAISS
        # index on disk, OR scan reports in the database. The chatbot can answer
        # scan questions straight from the DB with no document uploaded.
        return bool(self.current_doc_id or self.faiss_db or self._scans_exist())

    def get_web_available(self) -> bool:
        return bool(BRAVE_API_KEY)

    def db_connected(self) -> bool:
        try:
            with self._pg() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            return True
        except Exception:
            return False
