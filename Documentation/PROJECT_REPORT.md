# Kiwi Sorter — AI-Powered Produce Inspection System
### Project Documentation & Technical Report

**Author:** Baboo Dhanabalan · Master of Applied Computing, Lincoln University, New Zealand (2025–2026)
**Status:** Goals 1–5 delivered · Goal 6 and a custom-model phase scoped as future work
**Last updated:** 16 June 2026

---

## 1. Executive summary

Kiwi Sorter is a solo, end-to-end produce-inspection system that takes a camera
feed of fruit and returns, in real time, what the fruit is, how big it is, and an
AI judgement of its quality, ripeness, defects and shelf life — culminating in a
hands-free **augmented-reality** view through XREAL One glasses where the
information is overlaid directly onto the real fruit.

The system spans three independently deployable parts that together form one
pipeline: a **computer-vision web platform**, a **retrieval-augmented (RAG)
reporting and chat layer**, and a **wearable AR client**. It was built for the
problem New Zealand's largest export sector actually has — grading produce at
speed — and demonstrates integration across hardware, networking, computer
vision, large-language-model APIs and real-time 3D rendering.

## 2. Project goals and status

| # | Goal | Status |
|---|------|--------|
| 1 | YOLO fruit detection | ✅ Delivered (apple, banana, orange via YOLOv8s) |
| 2 | Claude vision quality analysis | ✅ Delivered (10-field structured output) |
| 3 | RAG report generation | ✅ Delivered (DocMindAI, FAISS + PostgreSQL) |
| 4 | RAG chatbot | ✅ Delivered (hybrid retrieval, citations, streaming) |
| 5 | XREAL AR glasses overlay | ✅ Delivered (v1.0, world-anchored, on-glass verified) |
| 6 | AR hand-tracking buttons | ⬜ Not started |
| — | Custom multi-task model (kiwi + ripeness) | ⬜ Scoped (see §9) |

## 3. System architecture

Three services run on a Windows laptop on the local network; the AR client runs
on a phone tethered to the glasses.

```
  XREAL Eye camera ──UVC──▶ Samsung S24 (Unity AR app) ──WiFi──┐
                                                                │
  Laptop webcam ──────────▶ Detection Platform (Flask :5000) ◀─┘  HTTP /detect, /ar-analyze
                                  │      │
                                  │      └── HTTPS :5443  (phone live-camera scanner)
                                  │
                                  ├── PostgreSQL 18 ── documents · chat_messages · scan_reports
                                  │
                            DocMindAI RAG (Flask :5001) ── FAISS index (on disk)
                                  │
                            Brave Search API (optional web blending)
```

- **Detection Platform** (`app.py`, port 5000) — webcam/Eye-camera detection,
  Claude quality analysis, the operator dashboard, the phone scanner, and the
  AR endpoints (`/detect`, `/ar-analyze`, `/ar-snapshot`, `/ar-record`,
  `/ar-toggle`).
- **DocMindAI RAG** (`DocMindAi Dai/app.py`, port 5001) — document upload &
  indexing, hybrid retrieval, grounded chat with inline citations, scan-history
  analytics.
- **AR client** (`KiwiSorterAR2022/`, Unity 2022.3 + XREAL SDK 3.0) — the
  optical see-through overlay.
- **PostgreSQL 18** — system of record for documents, chat history and scans.
  **FAISS** on disk is the vector store.

A single control panel — `Kiwi Sorter.hta` — starts/stops both servers and
shows live status. `setup_admin.bat` does one-time firewall + database setup.

## 4. Component 1 — Detection Platform (Flask, port 5000)

**Pipeline:** frame → YOLOv8s detection (conf 0.30 dashboard / 0.20 AR) → GrabCut
contour + pixel-to-cm measurement → Claude Vision analysis → structured result →
dashboard table + PostgreSQL.

- **Detection:** Ultralytics YOLOv8s (COCO) for apple/banana/orange; thread-locked
  inference (Ultralytics is not thread-safe under Flask's threaded server).
- **Measurement:** GrabCut segmentation + a calibratable px/cm ratio → width,
  height, area, diameter in centimetres.
- **Quality analysis:** Claude Vision (`claude-opus-4-5`) returns 10 structured
  fields — fruit type, quality, decay stage, days remaining, sort priority,
  colour, size category, defects, recommendation, confidence.
- **Interfaces:** a dark-theme operator dashboard (live MJPEG feed, results table,
  system-log SSE stream, camera/HSV/device settings); a **phone scanner** served
  over HTTPS (self-signed cert auto-generated per LAN IP) so the phone's live
  camera is allowed by the browser, with a photo-mode fallback over plain HTTP.
- **AR service:** a fast path (`/detect`, YOLO only, ~70 ms) and a deep path
  (`/ar-analyze`, one Claude call on the largest fruit), plus snapshot/record
  endpoints that burn the boxes and quality panel onto saved media, a master
  on/off toggle, and a UDP discovery beacon (port 5006) so the glasses find the
  server on any WiFi without a hardcoded IP.

## 5. Component 2 — DocMindAI RAG (Flask, port 5001)

A document-intelligence layer that ingests inspection reports (and any PDF/DOCX/
TXT) and answers grounded questions about them.

- **Ingestion:** documents are hashed (SHA-256 dedupe), split (600/100 chunks),
  embedded with `all-MiniLM-L6-v2`, and stored in FAISS; metadata and a cached
  Claude summary live in PostgreSQL.
- **Hybrid retrieval:** dense semantic search (FAISS) is fused with sparse
  keyword search (**BM25 over the whole corpus**) via **reciprocal-rank fusion**,
  returning the top passages with far better recall than either alone.
- **Grounded answers:** Claude (`claude-haiku-4-5`, prompt-cached) answers from
  numbered passages and cites them inline as `[1]`, `[2]`…; the UI turns each
  citation into a chip that scrolls to and flashes the source card.
- **Multi-source routing:** the assistant is aware of three knowledge sources —
  uploaded documents, the live fruit-scan database, and optional web search — and
  routes each question to the right one (a fruit question is answered from exact
  SQL counts, a document question from its excerpts), with the routing persisting
  across follow-up questions.
- **Document Library:** list, scope (limit answers to selected documents), and
  delete documents (vectors removed from FAISS, not just hidden).
- **Analytics:** an SQL-aggregated dashboard (totals, quality/type distributions,
  daily activity) that also feeds exact counts into counting questions — fixing
  the classic RAG failure where "how many" is answered from a 5-passage sample.
- **Streaming:** answers stream token-by-token over Server-Sent Events, with a
  non-streaming fallback.

## 6. Component 3 — AR client (Unity, XREAL One)

Optical see-through AR: bounding boxes and a quality card are drawn on a
transparent layer over the real world seen through the glasses.

- **Hardware chain:** XREAL Eye camera (UVC) → Samsung S24 (USB-C) → laptop over
  5 GHz WiFi; XREAL One glasses, optical see-through, **3DoF** (rotation only).
- **Core technique — world-anchored projection:** each detection's image position
  is converted, using the head pose and the Eye-camera field of view, into a
  real-world 3D point; every frame Unity's XR camera projects that point back to
  the screen. Because the anchor is fixed in the world, the box **stays on the
  apple as the head turns**. Motion is smoothed with a frame-rate-independent
  exponential filter plus a catch-up boost so a moving fruit is chased without
  jitter.
- **On-glass controls (IMGUI):** REC (server-assembled MP4), SNAP (annotated
  "what I see" image saved to the laptop), AI on/off (toggles the Claude path),
  and a live calibration panel (FOV, box size, smoothing).
- **Robustness:** server auto-discovery on any WiFi, one-press quit via the home
  button, pooled overlay panels (no per-frame allocation), and a camera watchdog.

## 7. Key engineering challenges solved

These are the non-trivial problems whose solutions define the project's depth:

1. **AR overlay in 3D space.** A flat screen-space canvas cannot map to the
   landscape optical FOV (the canvas is portrait, phone-shaped). Solved by
   anchoring detections as world points and letting Unity's projection handle
   FOV/aspect — after ruling out screen-space math, gyro reprojection and
   velocity prediction. Tracking mode had to be 3DoF (6DoF SLAM drifts on a plain
   table; 0DoF gives no head data).
2. **Multi-fruit GC stall.** A bag of fruit made the detection count flip every
   frame, and per-frame panel create/destroy caused garbage-collection spikes
   that progressively froze the app. Solved with a fixed panel pool.
3. **Network portability.** A hardcoded server IP broke whenever the laptop
   changed WiFi. Solved with a UDP discovery beacon that replies with the
   server's current, per-request IP.
4. **RAG correctness.** Counting questions were answered from a 5-passage sample
   (wrong at scale) and scan data was a second-class source. Solved by injecting
   exact SQL aggregates and making the scan database a first-class, intent-routed
   source.
5. **Server concurrency.** The deep Claude path was starving the 5 req/s
   detection path. Solved with a lightweight AR analysis path and thread-locked
   inference.

## 8. Results

- Detection ~70 ms/frame; fast AR path runs at 5 req/s with smooth overlay.
- AR overlay verified on-glass (v1.0): box locks to the real fruit, stays on it
  through head movement, quality card readable, no environment shell.
- RAG: hybrid retrieval with inline citations; counting questions answered from
  exact SQL (e.g. correct fruit/quality breakdowns across the scan database);
  first streamed token in ~2 s.
- One-command start/stop; clean, demo-ready analytics after data cleanup.

## 9. Known limitations & future work

- **Clustered fruit & kiwi.** Stock COCO YOLO under-detects overlapping fruit and
  has no kiwi class. The planned fix is a **Detectron2 Mask R-CNN** with a custom
  **multi-task ripeness head** (a small CNN+MLP regression branch on the shared
  backbone), with the ripeness training labels **distilled from Claude**, run as a
  hybrid alongside Claude for the rich fields. This closes the project's main
  ML-depth gap and fixes both detection problems via instance segmentation. Full
  plan in `ML_upgrade_plan.md`.
- **AR latency** (~0.5 s round trip) trails fast head motion; on-device inference
  would remove it.
- **Goal 6** (AR hand-tracking buttons) not started.

## 10. Technology stack

| Layer | Technology |
|-------|-----------|
| Detection | YOLOv8s (Ultralytics), OpenCV, GrabCut |
| LLM | Anthropic Claude — Opus 4.5 (vision), Haiku 4.5 (RAG) |
| RAG | FAISS, sentence-transformers (MiniLM), rank-bm25, reciprocal-rank fusion, Brave Search |
| Backend | Python, Flask, Server-Sent Events, MJPEG |
| Data | PostgreSQL 18 |
| AR | Unity 2022.3 LTS, XREAL SDK 3.0, AR Foundation 5.1, C# |
| Hardware | XREAL One glasses, XREAL Eye camera, Samsung S24, Windows laptop |

## 11. Running the system

1. **One-time:** double-click `setup_admin.bat` (firewall + PostgreSQL).
2. **Start:** double-click `Kiwi Sorter.hta` → Start System → wait for green dots.
3. Dashboard `http://localhost:5000`, RAG chat `http://localhost:5001`,
   phone scanner `https://<laptop-ip>:5443/mobile`.
4. **Stop:** Stop System in the control panel (or `stop_all.bat`).
5. AR: launch the installed app via ControlGlasses; it auto-discovers the server.
