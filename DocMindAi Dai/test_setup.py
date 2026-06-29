# test_setup.py — Run this BEFORE starting app.py to verify everything is working
# Usage: python test_setup.py
# Each test prints PASS or FAIL with a clear reason.

import sys
import os

# Load .env first
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("FAIL  python-dotenv not installed. Run: pip install python-dotenv")
    sys.exit(1)

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
INFO = "\033[94m  INFO\033[0m"

results = []

def check(label, fn):
    try:
        msg = fn()
        print(f"{PASS}  {label}" + (f"  →  {msg}" if msg else ""))
        results.append(True)
    except Exception as e:
        print(f"{FAIL}  {label}  →  {e}")
        results.append(False)


print("\n" + "="*60)
print("  DocMind AI v2.0 — Setup Verification")
print("="*60 + "\n")

# ── 1. Environment variables ──────────────────────────────────
print("[ 1 ]  Environment variables")

check("ANTHROPIC_API_KEY set",
      lambda: "Found: sk-ant-…" + os.environ["ANTHROPIC_API_KEY"][-6:]
      if os.environ.get("ANTHROPIC_API_KEY") else (_ for _ in ()).throw(
          EnvironmentError("Not set in .env")))

def _check_pg():
    v = os.environ.get("PG_CONNECTION", "")
    if not v:
        raise EnvironmentError("Not set in .env")
    if "yourpassword" in v:
        raise EnvironmentError("Still has placeholder 'yourpassword' — update .env")
    return v.split("@")[-1]   # show host:port/db only (hides password)
check("PG_CONNECTION set", _check_pg)

brave = os.environ.get("BRAVE_API_KEY", "")
print(f"{INFO}  BRAVE_API_KEY {'set — web search enabled' if brave else 'NOT set — web search will be disabled (OK)'}")


# ── 2. Python packages ────────────────────────────────────────
print("\n[ 2 ]  Python packages")

check("psycopg2",       lambda: __import__("psycopg2") and "ok")
check("faiss",          lambda: __import__("faiss") and "ok")
check("anthropic",      lambda: __import__("anthropic") and "ok")
check("flask",          lambda: __import__("flask") and "ok")
check("langchain_community", lambda: __import__("langchain_community") and "ok")
check("sentence_transformers", lambda: __import__("sentence_transformers") and "ok")
check("numpy",          lambda: __import__("numpy") and "ok")


# ── 3. PostgreSQL connection ──────────────────────────────────
print("\n[ 3 ]  PostgreSQL connection")

def _pg_connect():
    import psycopg2
    conn = psycopg2.connect(os.environ["PG_CONNECTION"])
    cur  = conn.cursor()
    cur.execute("SELECT version()")
    ver = cur.fetchone()[0].split(",")[0]
    conn.close()
    return ver
check("Connect to PostgreSQL", _pg_connect)


# ── 4. FAISS vector store ─────────────────────────────────────
print("\n[ 4 ]  FAISS vector store (disk-persistent)")

def _faiss_works():
    from langchain_community.vectorstores import FAISS
    from langchain_community.embeddings import HuggingFaceEmbeddings
    from langchain_core.documents import Document
    import tempfile, os
    emb  = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2",
                                  model_kwargs={"device": "cpu"})
    docs = [Document(page_content="test chunk", metadata={"doc_id": "x", "chunk_index": 0})]
    db   = FAISS.from_documents(docs, emb)
    with tempfile.TemporaryDirectory() as tmp:
        db.save_local(tmp)
        db2 = FAISS.load_local(tmp, emb, allow_dangerous_deserialization=True)
    results = db2.similarity_search("test", k=1)
    if not results:
        raise RuntimeError("FAISS search returned no results")
    return "create → save → load → search OK"
check("FAISS save/load/search works", _faiss_works)


# ── 5. Database tables ────────────────────────────────────────
print("\n[ 5 ]  Database tables (PostgreSQL)")

EXPECTED_TABLES = ["documents", "chat_messages", "scan_reports"]

def _tables():
    import psycopg2
    conn = psycopg2.connect(os.environ["PG_CONNECTION"])
    cur  = conn.cursor()
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' ORDER BY table_name"
    )
    existing = {r[0] for r in cur.fetchall()}
    conn.close()
    missing  = [t for t in EXPECTED_TABLES if t not in existing]
    if missing:
        raise RuntimeError(
            f"Missing tables: {missing}\n"
            "  Fix: Open pgAdmin → Query Tool on kiwi_sorter → run db_setup.sql"
        )
    return f"Found: {', '.join(sorted(existing & set(EXPECTED_TABLES)))}"
check("All required tables present", _tables)


# ── 6. Embedding model ────────────────────────────────────────
print("\n[ 6 ]  Embedding model (slow first run — downloads ~90 MB once)")

def _embed():
    from langchain_community.embeddings import HuggingFaceEmbeddings
    import numpy as np
    emb  = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2",
        model_kwargs={"device": "cpu"},
    )
    vec  = emb.embed_query("test sentence")
    if len(vec) != 384:
        raise ValueError(f"Expected 384 dims, got {len(vec)}")
    return f"384-dim vector generated OK"
check("HuggingFace all-MiniLM-L6-v2 loads and embeds", _embed)


# ── 7. Anthropic API (quick ping) ────────────────────────────
print("\n[ 7 ]  Anthropic API")

def _claude_ping():
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=10,
        messages=[{"role": "user", "content": "Reply with just the word: OK"}],
    )
    reply = msg.content[0].text.strip()
    return f"claude-haiku-4-5 replied: '{reply}'"
check("Anthropic API call succeeds", _claude_ping)


# ── Summary ───────────────────────────────────────────────────
print("\n" + "="*60)
passed = sum(results)
total  = len(results)
if passed == total:
    print(f"\033[92m  All {total} checks passed — run: python app.py\033[0m")
else:
    failed = total - passed
    print(f"\033[91m  {failed} check(s) failed — fix the FAIL items above before starting app.py\033[0m")
print("="*60 + "\n")
