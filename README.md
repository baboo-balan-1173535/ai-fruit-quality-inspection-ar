# 🥝 Kiwi Sorter — AI-Powered Produce Inspection System

An end-to-end system that detects fruit, judges its quality with AI, and overlays
the result directly onto the real fruit through **XREAL One AR glasses** — built
for the produce-grading problem at the heart of New Zealand's largest export
sector.

> Solo project · Master of Applied Computing, Lincoln University NZ (2025–2026)

![architecture](Documentation/Arictch.png)

## What it does

Point a camera at fruit and the system tells you, in real time, **what it is,
how big it is, and an AI judgement of its quality, ripeness, defects and shelf
life** — on an operator dashboard, on a phone, and hands-free through AR glasses.
A retrieval-augmented chat layer then answers questions about the scan history
and any uploaded documents.

## One system, three repositories

| Component | Stack | Repository |
|-----------|-------|------------|
| **Detection Platform** (this repo) | Flask · YOLOv8 · OpenCV · Claude Vision | Real-time detection, measurement, quality analysis, dashboard, phone scanner, AR endpoints |
| **DocMindAI RAG** | Flask · FAISS · PostgreSQL · Claude · BM25 | [docmindai-rag-chatbot](https://github.com/baboo-balan-1173535/docmindai-rag-chatbot) — document Q&A and scan-history chat with citations, hybrid retrieval, analytics |
| **AR Client** | Unity · C# · XREAL SDK | [xreal-ar-fruit-inspection](https://github.com/baboo-balan-1173535/xreal-ar-fruit-inspection) — world-anchored optical see-through overlay on XREAL One glasses |

The AR client and RAG service both talk to this platform's Flask server. Full
system technical report:
[`Documentation/PROJECT_REPORT.md`](Documentation/PROJECT_REPORT.md).

## Quick start

```text
1. setup_admin.bat        (once — firewall + PostgreSQL, self-elevating)
2. Kiwi Sorter.hta        (double-click → Start System)
3. Dashboard  http://localhost:5000
   RAG chat   http://localhost:5001
   Phone      https://<laptop-ip>:5443/mobile
4. Stop System  (or stop_all.bat)
```

## Highlights

- **World-anchored AR** that keeps a box glued to real fruit as your head turns
  (3DoF, optical see-through — no video passthrough).
- **Hybrid RAG** (dense + BM25, reciprocal-rank fusion) with **inline citations**
  and **SQL-exact** answers to counting questions.
- **Multi-source routing** — the chatbot knows whether a question is about a
  document, the scan database, or the web, and answers from the right one.
- **Zero-config networking** — the glasses discover the server over UDP on any WiFi.

## Status & roadmap

Goals 1–5 delivered (detection, AI quality, RAG reports, RAG chat, AR overlay).
In development: a custom-trained instance-segmentation model with a ripeness
head (kiwifruit support + clustered-fruit separation — see `ml/`). Planned: AR
hand-tracking and PLC/conveyor integration.

## License

**All rights reserved.** This is a personal portfolio project published for
review and demonstration only — no permission is granted to reuse, copy,
modify, or redistribute any part of the code.
