"""
Unit tests for DocMindAI's retrieval logic — the genuinely testable core
(tokeniser, reciprocal-rank fusion, web-search gating). These run without a
database or network and don't require the embedding model.

Run from the DocMindAI venv:
    .venv\\Scripts\\python -m pytest ../tests -q
"""
import os
import sys

# Make the DocMindAI package importable regardless of where pytest is invoked
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "DocMindAi Dai"))

from rag_engine import RAGEngine, brave_search  # noqa: E402


class _Doc:
    """Minimal stand-in for a LangChain Document (metadata + page_content)."""
    def __init__(self, doc_id, chunk_index, text="x"):
        self.metadata = {"doc_id": doc_id, "chunk_index": chunk_index}
        self.page_content = text


# ── Tokeniser ────────────────────────────────────────────────────────────────

def test_tok_lowercases_and_drops_short_words():
    assert RAGEngine._tok("The Quick a IS Fox") == ["the", "quick", "fox"]


def test_tok_empty():
    assert RAGEngine._tok("a is to") == []   # all <= 2 chars


# ── Reciprocal-rank fusion ───────────────────────────────────────────────────

def test_rrf_rewards_agreement():
    """A doc appearing in BOTH rankings should beat one in only a single list."""
    d1, d2, d3, d4 = _Doc("D", 1), _Doc("D", 2), _Doc("D", 3), _Doc("D", 4)
    semantic = [d1, d2, d3]   # d2 is rank 1 here
    keyword  = [d2, d4]       # d2 is rank 0 here  → appears in both
    fused = RAGEngine._rrf_fuse([semantic, keyword], top_n=5)
    assert fused[0] is d2      # agreement wins


def test_rrf_dedupes_by_identity():
    """The same (doc_id, chunk_index) must appear once, not twice."""
    d1, d2 = _Doc("D", 1), _Doc("D", 2)
    fused = RAGEngine._rrf_fuse([[d1, d2], [d1]], top_n=5)
    keys = [(d.metadata["doc_id"], d.metadata["chunk_index"]) for d in fused]
    assert len(keys) == len(set(keys))   # no duplicates
    assert ("D", 1) in keys and ("D", 2) in keys


def test_rrf_respects_top_n():
    docs = [_Doc("D", i) for i in range(10)]
    fused = RAGEngine._rrf_fuse([docs], top_n=3)
    assert len(fused) == 3


# ── Web search gating ────────────────────────────────────────────────────────

def test_brave_search_without_key_reports_unavailable():
    """No API key configured → (no results, web_available=False)."""
    import rag_engine
    saved = rag_engine.BRAVE_API_KEY
    rag_engine.BRAVE_API_KEY = ""
    try:
        results, available = brave_search("anything")
        assert results == [] and available is False
    finally:
        rag_engine.BRAVE_API_KEY = saved
