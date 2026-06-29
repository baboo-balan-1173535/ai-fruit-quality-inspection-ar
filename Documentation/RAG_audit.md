# DocMindAI (RAG) — Functional Audit, 12 Jun 2026

## WORKING (keep)
Upload (4 entry points, hash-dedup, chunk 600/100, FAISS+PG registry) · Claude summary on upload ·
Chat /ask (FAISS k=5 + TF-IDF hybrid, dedup, 10-msg history, Haiku + prompt caching) ·
Web toggle (Brave) · Source cards (page no, expand, web links) · Clear Chat (UI + PG) ·
Scan History loader (/load-scans: PG scan_reports -> text docs -> FAISS + stats md) ·
Status badge (load-time only) · Doc info · sidebar/section collapse · About modal ·
GET /history endpoint (works, UI never calls it)

## DECORATIVE (no handlers)
Nav Workspace/Summary/Sources (highlight only -> wire to scroll) · Library (dead; documents table exists -> BUILD) ·
Analytics (dead -> BUILD with SQL stats) · Help, Sign Out (REMOVE) ·
Header tabs Documents/Knowledge Base/Research (REMOVE) · Notifications/Settings/Profile icons (REMOVE)

## DESIGN FLAWS
1. current_doc_id is server-GLOBAL (not per session) — last upload wins for everyone.
2. Semantic search = whole index, keyword = latest doc only -> silent cross-doc mixing; no doc scoping.
3. No list/delete: FAISS grows forever; Scan History re-adds duplicate scan docs each click.
4. Aggregate questions ("how many good apples") use top-5 retrieval -> wrong at scale; needs SQL path.
5. Re-upload of indexed doc still pays Claude summary; status never re-polls; no inline citations;
   chat history stored in PG but not restored into UI on page load.

## ROADMAP
Phase 1 (honesty): remove dead chrome; nav->scroll; poll /status 10s; restore history on load; cache summaries in PG.
Phase 2 (proper RAG): Document Library (list/select/delete + metadata-filtered retrieval);
 inline [1][2] citations mapped to source cards; rank_bm25 over ALL chunks + RRF fusion (+optional cross-encoder rerank);
 Analytics page = SQL over scan_reports (fixes counting) + charts; SSE streaming answers.
Phase 3 (hardening): eval set, upload cleanup, rate limit, Dockerfile, optional pgvector.
