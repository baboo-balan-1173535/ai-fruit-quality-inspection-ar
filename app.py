import cv2
import os
import json
import uuid
import time
import socket
import base64
import threading
import queue
import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from flask import Flask, render_template, Response, request, jsonify, make_response
from flask_cors import CORS
from camera import get_camera, capture_frame
from detector import (detect_fruits, detect_fruits_fast, draw_contour_on_frame,
                      get_dominant_color, get_contour_grabcut)
from analyser import analyse_fruit

load_dotenv()

app = Flask(__name__)
CORS(app)   # Allow Unity / phone browser to call from a different IP on the LAN

# ── LAN IP (detected once on startup) ──────────────────────
def _get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

LAN_IP = _get_lan_ip()

# ── Config file helpers ─────────────────────────────────────
def _load_config():
    """Read config.json ({} if missing/corrupt)."""
    try:
        with open('config.json') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config_key(key, value):
    """Read-modify-write a single key in config.json (preserves other keys)."""
    cfg = _load_config()
    cfg[key] = value
    with open('config.json', 'w') as f:
        json.dump(cfg, f)


# ── Security ────────────────────────────────────────────────
# AR_TOKEN  : required in X-AR-Token header for /detect and /ar-analyze
# Device allowlist: managed at runtime from the dashboard (Settings > Devices)
#   and persisted in config.json. .env ALLOWED_IPS still works as a seed —
#   if set, enforcement starts ON with those IPs pre-approved.
#   localhost is ALWAYS allowed, so you can never lock yourself out of the
#   dashboard on the laptop.
AR_TOKEN    = os.environ.get("AR_TOKEN", "")
_raw_ips    = os.environ.get("ALLOWED_IPS", "")
_env_ips    = {ip.strip() for ip in _raw_ips.split(",") if ip.strip()}
_cfg_boot   = _load_config()
ALLOWED_IPS = set(_cfg_boot.get("allowed_ips", [])) | _env_ips
ENFORCE_IPS = bool(_cfg_boot.get("enforce_ips", bool(_env_ips)))

_seen_devices = {}   # ip -> {"first_seen": ts, "last_seen": ts, "blocked": n}
_devices_lock = threading.Lock()


@app.before_request
def track_and_enforce():
    """Record every connecting device; block non-allowed IPs when enforcing."""
    if request.method == "OPTIONS":
        return None          # let CORS preflight through
    client  = request.remote_addr or "?"
    local   = client in ("127.0.0.1", "::1") or client == LAN_IP
    allowed = local or client in ALLOWED_IPS or not ENFORCE_IPS

    # Track remote devices (mobile scanner, AR app) for the Devices settings UI
    if not local and not request.path.startswith('/static'):
        with _devices_lock:
            d = _seen_devices.setdefault(
                client, {"first_seen": time.time(), "last_seen": 0.0, "blocked": 0})
            d["last_seen"] = time.time()
            if not allowed:
                d["blocked"] += 1

    if allowed:
        return None
    return jsonify({
        "error":   "Device not authorised.",
        "your_ip": client,
        "fix":     "On the laptop dashboard open Settings > Devices and Allow this IP.",
    }), 403


def _require_ar_token():
    """
    Returns a 401 response if AR_TOKEN is set but the request
    doesn't provide it via the X-AR-Token header.
    Returns None when the check passes.
    """
    if not AR_TOKEN:
        return None     # no token configured — skip check
    provided = (request.headers.get("X-AR-Token") or
                request.form.get("token") or
                request.args.get("token"))
    if provided != AR_TOKEN:
        return jsonify({
            "error": "Missing or invalid AR token.",
            "hint":  "Add  X-AR-Token: <your_token>  to the request headers.",
        }), 401
    return None


# ── PostgreSQL ─────────────────────────────────────────────
PG_CONNECTION = os.environ.get(
    "PG_CONNECTION",
    "postgresql://postgres:password@localhost:5432/kiwi_sorter"
)
# One UUID per app-run — groups all scans from this session together
SESSION_ID = str(uuid.uuid4())

# Camera index can be overridden in .env — avoids needing the UI modal
# when a WiFi camera or another app occupies index 0.
# Run camera_test.py to find the correct index for your setup.
# Camera index priority: last UI selection (config.json) > .env CAMERA_INDEX > 0.
# The UI choice is persisted by /switch_camera so it survives restarts.
_DEFAULT_CAM = int(_load_config().get(
    "camera_index", os.environ.get("CAMERA_INDEX", "0")))


def _pg():
    """Open a psycopg2 connection. Caller must close or use as context manager."""
    return psycopg2.connect(PG_CONNECTION)


def _save_scan_report(scan_image_path: str, results: list, mode: str = "legacy"):
    """
    Persist a completed scan to the scan_reports table.
    crop_b64 thumbnails are stripped before storage — they are large and
    can be regenerated from the saved image file if needed.
    """
    try:
        db_results = [
            {k: v for k, v in r.items() if k != "crop_b64"}
            for r in results
        ]
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO scan_reports
                        (session_id, scan_image, fruit_count, results, mode)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        SESSION_ID,
                        scan_image_path,
                        len(results),
                        json.dumps(db_results),
                        mode,
                    ),
                )
                report_id = str(cur.fetchone()[0])
        print(f"[DB] Scan saved → report_id={report_id[:8]}…")
        return report_id
    except Exception as e:
        print(f"[DB] save_scan_report error: {e}")
        return None


# ── Global state ───────────────────────────────────────────
camera               = None
camera_lock          = threading.Lock()
scan_history         = []
latest_frame         = None
log_queue            = queue.Queue(maxsize=200)
current_camera_index = _DEFAULT_CAM   # set CAMERA_INDEX in .env to change
hsv_overrides        = {}


# ══════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════

_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs', 'server.log')
os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)


def log(msg):
    print(msg)
    try:
        log_queue.put_nowait(msg)
    except queue.Full:
        pass
    # Persistent copy — survives the console window closing, so freezes and
    # crashes can be diagnosed after the fact. Trimmed when it grows too big.
    try:
        stamp = time.strftime('%H:%M:%S')
        with open(_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{stamp} {msg}\n")
        if os.path.getsize(_LOG_FILE) > 5_000_000:
            with open(_LOG_FILE, 'r', encoding='utf-8', errors='ignore') as f:
                tail = f.readlines()[-2000:]
            with open(_LOG_FILE, 'w', encoding='utf-8') as f:
                f.writelines(tail)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════
#  CAMERA
# ══════════════════════════════════════════════════════════

def get_cam():
    global camera
    if camera is None or not camera.isOpened():
        camera = get_camera(index=current_camera_index)
    return camera


# ── Live-stream detection worker ───────────────────────────
# The MJPEG generator used to run detect_fruits (YOLO + GrabCut per fruit) on
# EVERY streamed frame. With no fruit in view that's cheap; the moment a fruit
# appears, GrabCut fires per frame (hundreds of ms) and the stream "freezes".
# Fix: one background thread runs the heavy pipeline ~3x/sec and caches the
# detections; the generator just draws the cached overlays on every fresh
# camera frame, so the stream stays at full fps regardless of detection cost.
_live_lock     = threading.Lock()
_live_frame    = None     # latest raw camera frame (shared with the worker)
_live_frame_ts = 0.0      # when that frame arrived
_live_dets     = []       # last completed detection results
_live_worker_started = False


def _live_detection_worker():
    """
    Two-tier live detection:
      - Tier 1 (every cycle, ~4x/sec): YOLO + measurements only — bounding box,
        label and size arrows update quickly when the fruit appears or moves.
      - Tier 2 (budgeted): GrabCut contours are expensive (hundreds of ms per
        fruit), so at most ONE contour is (re)computed per cycle. Contours are
        crop-relative and draw_contour_on_frame shifts them by the bbox origin,
        so a cached contour follows a moving fruit automatically; its shape is
        refreshed when missing or older than 2s.
    """
    global _live_dets
    prev = []
    while True:
        with _live_lock:
            fresh = _live_frame is not None and (time.time() - _live_frame_ts) < 1.0
            frame = _live_frame.copy() if fresh else None
        if frame is None:
            # No active stream (camera tab closed / no clients) — idle cheaply.
            time.sleep(0.10)
            continue

        try:
            dets = detect_fruits(frame, with_contours=False, with_color=False)
            now  = time.time()

            # Carry over cached contours from the previous cycle (match by fruit
            # type + nearby centre + similar box size).
            for det in dets:
                x, y, w, h = det["bbox"]
                cx, cy = x + w / 2.0, y + h / 2.0
                best, best_d = None, max(w, h) * 0.6
                for p in prev:
                    if p["fruit_type"] != det["fruit_type"]:
                        continue
                    if p.get("contour_points") is None:
                        continue
                    px, py, pw, ph = p["bbox"]
                    d = ((cx - (px + pw / 2.0)) ** 2 + (cy - (py + ph / 2.0)) ** 2) ** 0.5
                    if d < best_d and 0.6 < (w * h) / max(1, pw * ph) < 1.6:
                        best, best_d = p, d
                if best is not None:
                    det["contour_points"] = best["contour_points"]
                    det["_contour_ts"]    = best.get("_contour_ts", 0.0)

            # PUBLISH IMMEDIATELY after YOLO — the box/label/size must not wait
            # for GrabCut. Box latency = one YOLO pass (feels instantaneous).
            prev = dets
            with _live_lock:
                _live_dets = dets

            # Contour budget AFTER publishing: one GrabCut per cycle — missing
            # contours first, then the stalest if older than 2s. Re-publish so
            # the new outline appears as soon as it's ready.
            cand = next((d for d in dets if d.get("contour_points") is None), None)
            if cand is None and dets:
                oldest = min(dets, key=lambda d: d.get("_contour_ts", 0.0))
                if now - oldest.get("_contour_ts", 0.0) > 2.0:
                    cand = oldest
            if cand is not None:
                x, y, w, h = cand["bbox"]
                crop = frame[y:y + h, x:x + w]
                if crop.size > 0:
                    cand["contour_points"] = get_contour_grabcut(crop)
                    cand["_contour_ts"]    = time.time()
                    with _live_lock:
                        _live_dets = dets
        except Exception as e:
            log(f"[live] detection error: {e}")
            with _live_lock:
                _live_dets = []

        # Tiny yield only — the loop should run at YOLO speed so box updates
        # feel instant, like the old per-frame version (without its freezes).
        time.sleep(0.03)


def _ensure_live_worker():
    global _live_worker_started
    if not _live_worker_started:
        _live_worker_started = True
        threading.Thread(target=_live_detection_worker, daemon=True).start()


def generate_frames():
    """
    Continuously reads camera frames, draws the latest cached detections
    (computed by the background worker), and streams as MJPEG at full fps.
    """
    global latest_frame, _live_frame, _live_frame_ts
    _ensure_live_worker()
    while True:
        with camera_lock:
            try:
                cap = get_cam()
                ret, frame = cap.read()
                if not ret:
                    continue
            except Exception:
                continue

        # Hand the worker a COPY of the raw frame (overlays get drawn onto
        # `frame` below — the worker must never see painted boxes), and take
        # the latest cached detections.
        with _live_lock:
            _live_frame    = frame.copy()
            _live_frame_ts = time.time()
            dets = list(_live_dets)

        # Draw cached contour + bbox overlays (refreshed ~3x/sec by the worker)
        for i, det in enumerate(dets):
            frame = draw_contour_on_frame(frame, det, i + 1)

        latest_frame = frame.copy()

        _, buffer = cv2.imencode('.jpg', frame)
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n'
               + buffer.tobytes() + b'\r\n')


def frame_to_base64(frame):
    _, buffer = cv2.imencode('.jpg', frame)
    return base64.b64encode(buffer).decode('utf-8')


# ══════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('index.html', lan_ip=_get_lan_ip())


@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/logs')
def logs():
    def event_stream():
        while True:
            msg = log_queue.get()
            yield 'data: {}\n\n'.format(msg)
    return Response(event_stream(), mimetype='text/event-stream')


@app.route('/analyse', methods=['POST'])
def analyse():
    if latest_frame is None:
        return jsonify({"error": "No camera frame available"}), 400
    return run_analysis(latest_frame.copy(), source="camera")


@app.route('/upload', methods=['POST'])
def upload():
    if 'image' not in request.files:
        return jsonify({"error": "No image uploaded"}), 400
    file       = request.files['image']
    file_bytes = file.read()
    np_arr     = np.frombuffer(file_bytes, np.uint8)
    frame      = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    if frame is None:
        return jsonify({"error": "Could not decode image"}), 400
    max_w, max_h = 1280, 720
    h, w = frame.shape[:2]
    if w > max_w or h > max_h:
        scale = min(max_w / w, max_h / h)
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
    return run_analysis(frame, source="upload")


# ══════════════════════════════════════════════════════════
#  CORE ANALYSIS
# ══════════════════════════════════════════════════════════

def run_analysis(frame, source="camera"):
    log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log("📸 New scan — source: {}".format(source))
    log("🔍 Running YOLO + contour detection...")

    detections = detect_fruits(frame)

    if not detections:
        log("⚠️  No fruits detected")
        return jsonify({
            "error": "No fruit detected. Try better lighting or a plain background."
        }), 404

    log("✅ Detected {} fruit(s): {}".format(
        len(detections),
        ', '.join(d['fruit_type'] for d in detections)
    ))

    results      = []
    result_frame = frame.copy()

    for i, det in enumerate(detections):
        log("  → Analysing {} ({:.1f}% confidence)...".format(
            det['fruit_type'], det['confidence']))

        try:
            analysis = analyse_fruit(
                det["cropped"],
                det["size_info"],
                det["dominant_color"],
                det["fruit_type"]
            )
            log("     ✔ {}: {} | {} | {} days".format(
                det['fruit_type'].upper(),
                analysis.get('QUALITY'),
                analysis.get('DECAY_STAGE'),
                analysis.get('DAYS_REMAINING')
            ))
        except Exception as e:
            log("     ✖ API error: {}".format(e))
            analysis = {"QUALITY": "Error", "error": str(e)}

        # Draw contour + bbox + measurements on result frame
        result_frame = draw_contour_on_frame(result_frame, det, i + 1)

        # Overwrite label now that we have quality from Claude
        x, y, w, h = det["bbox"]
        color       = det["display_color"]
        label       = '[{}] {} {}% | {}'.format(
            i + 1,
            det['fruit_type'].upper(),
            det['confidence'],
            analysis.get('QUALITY', '?')
        )
        (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.50, 2)
        cv2.rectangle(result_frame, (x, y - lh - 10), (x + lw + 8, y), color, -1)
        cv2.putText(result_frame, label, (x + 4, y - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.50, (255, 255, 255), 2)

        size = det["size_info"]
        results.append({
            "index":          i + 1,
            "fruit":          det["fruit_type"],
            "confidence":     det["confidence"],
            "quality":        analysis.get("QUALITY", "?"),
            "decay_stage":    analysis.get("DECAY_STAGE", "?"),
            "days":           analysis.get("DAYS_REMAINING", "?"),
            "sort":           analysis.get("SORT_PRIORITY", "?"),
            "size":           analysis.get("SIZE_CATEGORY", "?"),
            # ── measurements ──────────────────────────────
            "width_cm":       size["width_cm"],
            "height_cm":      size["height_cm"],
            "area_cm2":       size["area_cm2"],
            "diameter_cm":    size["estimated_diameter_cm"],
            # ── other ──────────────────────────────────────
            "defects":        analysis.get("DEFECTS", "None"),
            "recommendation": analysis.get("RECOMMENDATION", ""),
            "confidence_ai":  analysis.get("CONFIDENCE", "?"),
            "color_desc":     analysis.get("COLOR_DESCRIPTION", ""),
            "bbox_color":     list(det["display_color"]),
            "crop_b64":       frame_to_base64(det["cropped"]),
        })

    os.makedirs("static/results", exist_ok=True)
    scan_id      = len(scan_history) + 1
    img_filename = "static/results/scan_{:03d}.jpg".format(scan_id)
    cv2.imwrite(img_filename, result_frame)

    # Persist to PostgreSQL (strips crop thumbnails — path saved instead)
    _save_scan_report(img_filename, results, mode="legacy")

    scan_entry = {
        "id":     scan_id,
        "source": source,
        "image":  frame_to_base64(result_frame),
        "fruits": results,
    }
    scan_history.append(scan_entry)

    log("💾 Saved → {}".format(img_filename))
    log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    return jsonify(scan_entry)


# ══════════════════════════════════════════════════════════
#  AR PIPELINE  —  /detect  +  /ar-analyze
# ══════════════════════════════════════════════════════════

# AR uses a LOWER confidence than the web dashboard (0.30): the Eye camera image
# is softer, and a fruit hovering at the threshold makes the AR box flicker.
AR_CONFIDENCE     = 0.20
_ar_frame_counter = 0   # throttles the /detect debug-frame save

# Master switch for the AR endpoints, controlled from the dashboard.
# When off, /detect and /ar-analyze answer 503 so the glasses app shows
# "AR disabled" instead of silently timing out.
AR_ENABLED = True

# Last contact from the AR glasses app — shown on the dashboard AR panel.
AR_LAST_SEEN = 0.0
AR_LAST_IP   = ""

# Diagnostics for the box-freeze issue: distinguishes a frozen Eye-camera feed
# (same frame arriving repeatedly) from network stalls (gaps between requests).
_ar_prev_thumb   = None   # tiny grayscale of the previous frame
_ar_same_count   = 0      # consecutive near-identical frames
_ar_prev_req_ts  = 0.0    # arrival time of the previous /detect request


def _ar_diagnose(frame):
    """Log when frames stop changing (camera freeze) or stop arriving (network)."""
    global _ar_prev_thumb, _ar_same_count, _ar_prev_req_ts
    now = time.time()
    if _ar_prev_req_ts and (now - _ar_prev_req_ts) > 1.0:
        log(f"⚠️ [AR] request gap {now - _ar_prev_req_ts:.1f}s — network stall or app pause")
    _ar_prev_req_ts = now

    thumb = cv2.cvtColor(cv2.resize(frame, (32, 18)), cv2.COLOR_BGR2GRAY).astype(np.int16)
    if _ar_prev_thumb is not None:
        diff = float(np.mean(np.abs(thumb - _ar_prev_thumb)))
        if diff < 0.5:
            _ar_same_count += 1
            if _ar_same_count in (10, 25) or _ar_same_count % 50 == 0:
                log(f"⚠️ [AR] {_ar_same_count} identical frames in a row — Eye camera feed looks FROZEN")
        else:
            if _ar_same_count >= 10:
                log(f"✅ [AR] Eye camera feed moving again after {_ar_same_count} frozen frames")
            _ar_same_count = 0
    _ar_prev_thumb = thumb


def _decode_image():
    """
    Extract and decode a JPEG image from multipart/form-data 'image' field.
    Returns (frame, None) on success or (None, error_string) on failure.
    Unity sends YUV→JPEG at 65% quality; phone browsers send JPEG directly.
    """
    if 'image' not in request.files:
        return None, "No 'image' field in request"
    file_bytes = request.files['image'].read()
    np_arr     = np.frombuffer(file_bytes, np.uint8)
    frame      = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    if frame is None:
        return None, "Could not decode image"
    # Cap resolution — keeps inference fast on laptop GPU/CPU
    max_w, max_h = 1280, 720
    h, w = frame.shape[:2]
    if w > max_w or h > max_h:
        scale = min(max_w / w, max_h / h)
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
    return frame, None


@app.route('/status')
def status():
    """Health-check used by the mobile page connection badge."""
    return jsonify({
        "status":       "ok",
        "ar_token_set": bool(AR_TOKEN),
        "allowed_ips":  bool(ALLOWED_IPS),
        "enforce_ips":  ENFORCE_IPS,
        "camera_index": current_camera_index,
        "lan_ip":       _get_lan_ip(),
        "ar_enabled":   AR_ENABLED,
        "ar_last_seen": round(time.time() - AR_LAST_SEEN, 1) if AR_LAST_SEEN else None,
        "ar_device_ip": AR_LAST_IP or None,
    })


# ── AR screen recording ─────────────────────────────────────
# The glasses already send frames 5x/sec via /detect, so recording = the server
# writing those frames (with boxes drawn) into an MP4. No extra glasses load.
_rec_lock    = threading.Lock()
_rec_writer  = None
_rec_path    = ""
_rec_frames  = 0
_rec_started = 0.0
REC_FPS      = 5
REC_MAX_SEC  = 300   # safety auto-stop

# Latest Claude quality result, cached from /ar-analyze so the recorder and
# snapshot can burn the same quality panel onto the frame that the glasses show.
# (The recording is built from /detect frames, which carry no quality data.)
_ar_last_qa    = None
_ar_last_qa_ts = 0.0


def _qa_summary_lines(qa):
    """One or two short overlay lines from a cached /ar-analyze quality dict."""
    if not qa:
        return []
    lbl = (qa.get("label") or "fruit").upper()
    line1 = f"{lbl}: {qa.get('quality','?')} | {qa.get('decay_stage','?')} | {qa.get('days_remaining','?')}d"
    lines = [line1]
    defects = qa.get("defects")
    if defects and defects != "None":
        d = defects if len(defects) <= 60 else defects[:60] + "..."
        lines.append("Defects: " + d)
    return lines


def _draw_qa_overlay(frame):
    """Burn the cached quality panel (if recent) onto a frame, top-left."""
    if not _ar_last_qa or time.time() - _ar_last_qa_ts > 8:
        return
    y = 30
    for line in _qa_summary_lines(_ar_last_qa):
        cv2.putText(frame, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (0, 0, 0), 4)               # black outline for contrast
        cv2.putText(frame, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (65, 255, 0), 2)            # green text (matches glasses)
        y += 30


def _rec_write(frame, detections):
    """Append an annotated copy of this /detect frame to the recording."""
    global _rec_writer, _rec_frames
    with _rec_lock:
        if _rec_writer is None and not _rec_path:
            return
        annotated = frame.copy()
        h, w = annotated.shape[:2]
        for d in detections:
            x1, y1 = int(d["bbox_norm"][0] * w), int(d["bbox_norm"][1] * h)
            x2, y2 = int(d["bbox_norm"][2] * w), int(d["bbox_norm"][3] * h)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (48, 48, 220), 2)
            tag = f'{d["label"]} {d["confidence"]:.0f}%'
            cv2.putText(annotated, tag, (x1 + 2, max(14, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (48, 48, 220), 2)
        _draw_qa_overlay(annotated)   # burn the quality panel onto the video too
        if _rec_writer is None:   # lazy init with the real frame size
            _rec_writer = cv2.VideoWriter(
                _rec_path, cv2.VideoWriter_fourcc(*'mp4v'), REC_FPS, (w, h))
        _rec_writer.write(annotated)
        _rec_frames += 1
    if time.time() - _rec_started > REC_MAX_SEC:
        _rec_stop()
        log(f"🎬 Recording auto-stopped at {REC_MAX_SEC}s")


def _rec_stop():
    """Finalize the recording. Returns (url, frames) or (None, 0)."""
    global _rec_writer, _rec_path, _rec_frames
    with _rec_lock:
        if not _rec_path:
            return None, 0
        if _rec_writer is not None:
            _rec_writer.release()
        path, frames = _rec_path, _rec_frames
        _rec_writer, _rec_path, _rec_frames = None, "", 0
    url = f"http://{_get_lan_ip()}:5000/{path.replace(os.sep, '/')}"
    log(f"🎬 Recording saved ({frames} frames) → {url}")
    return url, frames


@app.route('/ar-record', methods=['POST'])
def ar_record():
    """Start/stop recording the AR session (assembled from /detect frames)."""
    global _rec_path, _rec_frames, _rec_started
    err = _require_ar_token()
    if err: return err

    data   = request.get_json(silent=True) or {}
    action = data.get('action', 'toggle')
    recording = bool(_rec_path)

    if action == 'stop' or (action == 'toggle' and recording):
        url, frames = _rec_stop()
        return jsonify({"recording": False, "url": url, "frames": frames})

    if not recording:
        rec_dir = os.path.join('static', 'results', 'recordings')
        os.makedirs(rec_dir, exist_ok=True)
        with _rec_lock:
            _rec_path    = os.path.join(rec_dir, time.strftime('rec_%Y%m%d_%H%M%S.mp4'))
            _rec_frames  = 0
            _rec_started = time.time()
        log("🎬 Recording STARTED (frames collected from /detect)")
    return jsonify({"recording": True})


@app.route('/ar-snapshot', methods=['POST'])
def ar_snapshot():
    """
    Save a shareable "what I see" picture from the glasses. The optical display
    can't be screen-captured (the real world isn't rendered), so the glasses
    send the current Eye-camera frame; we draw the detection boxes + quality
    text onto it and save the JPEG under static/results/snapshots/.
    """
    err = _require_ar_token()
    if err: return err

    frame, err = _decode_image()
    if err:
        return jsonify({"error": err}), 400

    # Boxes: prefer the ones the GLASSES are currently showing (sent with the
    # request) so the snapshot matches what the user saw — a fresh server-side
    # detection can miss a fruit the on-glass tracker was still holding.
    # Format: "label,conf,x1,y1,x2,y2;label,conf,..."  (normalised 0-1 coords)
    boxes = []
    for chunk in (request.form.get('boxes') or "").split(';'):
        p = chunk.split(',')
        if len(p) == 6:
            try:
                boxes.append({"label": p[0], "confidence": float(p[1]),
                              "bbox_norm": [float(v) for v in p[2:6]]})
            except ValueError:
                pass
    if not boxes:   # fallback: detect fresh
        boxes = detect_fruits_fast(frame, conf=AR_CONFIDENCE)

    h, w = frame.shape[:2]
    for d in boxes:
        x1, y1, x2, y2 = (int(d["bbox_norm"][0] * w), int(d["bbox_norm"][1] * h),
                          int(d["bbox_norm"][2] * w), int(d["bbox_norm"][3] * h))
        cv2.rectangle(frame, (x1, y1), (x2, y2), (48, 48, 220), 3)
        tag = f'{d["label"]} {d["confidence"]:.0f}%'
        cv2.rectangle(frame, (x1, max(0, y1 - 28)), (x1 + 11 * len(tag), y1), (48, 48, 220), -1)
        cv2.putText(frame, tag, (x1 + 4, max(14, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    # Quality summary: prefer what the glasses sent; else fall back to the
    # latest cached /ar-analyze result so the snapshot still shows the panel.
    info = (request.form.get('info') or "").strip()
    if info:
        for li, line in enumerate(info.split('|')[:3]):
            cv2.putText(frame, line.strip(), (10, 28 + 30 * li),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
            cv2.putText(frame, line.strip(), (10, 28 + 30 * li),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (65, 255, 0), 2)
    else:
        _draw_qa_overlay(frame)

    snap_dir = os.path.join('static', 'results', 'snapshots')
    os.makedirs(snap_dir, exist_ok=True)
    fname = time.strftime('snap_%Y%m%d_%H%M%S.jpg')
    cv2.imwrite(os.path.join(snap_dir, fname), frame)
    url = f"http://{_get_lan_ip()}:5000/static/results/snapshots/{fname}"
    log(f"📸 AR snapshot saved → {url}")
    return jsonify({"saved": True, "file": fname, "url": url})


@app.route('/ar-toggle', methods=['POST'])
def ar_toggle():
    """Dashboard switch for the AR endpoints (/detect + /ar-analyze)."""
    global AR_ENABLED
    data = request.get_json(silent=True) or {}
    AR_ENABLED = bool(data.get('enabled', not AR_ENABLED))
    log(f"🥽 AR endpoints {'ENABLED' if AR_ENABLED else 'DISABLED'} from dashboard")
    return jsonify({"ar_enabled": AR_ENABLED})


@app.route('/mobile')
def mobile():
    """Mobile-optimised scanner page — uses the phone's own camera."""
    # no-store: phone browsers cache aggressively over plain HTTP, which has
    # served stale copies of this page after edits
    resp = make_response(render_template('mobile.html', ar_token=AR_TOKEN, lan_ip=_get_lan_ip()))
    resp.headers['Cache-Control'] = 'no-store, must-revalidate'
    return resp


@app.route('/detect', methods=['POST'])
def ar_detect():
    """
    Fast pipeline — YOLO + OpenCV only. Target: ~30-60ms.
    Unity calls this every 500ms to update AR bounding box overlays.

    Accepts: multipart/form-data  {'image': <JPEG bytes>}
    Returns:
        {
            "detections": [
                {"label": "apple", "confidence": 97.2,
                 "bbox_norm": [x1,y1,x2,y2],   <- normalised 0-1
                 "width_cm": 8.2, "height_cm": 7.9}
            ],
            "ts": 1718234567.891
        }
    """
    global AR_LAST_SEEN, AR_LAST_IP
    AR_LAST_SEEN = time.time()
    AR_LAST_IP   = request.remote_addr

    if not AR_ENABLED:
        return jsonify({"error": "AR disabled from dashboard",
                        "detections": [], "ts": time.time()}), 503

    err = _require_ar_token()
    if err: return err

    frame, err = _decode_image()
    if err:
        return jsonify({"error": err, "detections": [], "ts": time.time()}), 400

    _ar_diagnose(frame)

    # DEBUG: save the incoming AR frame so we can inspect what the Eye sent.
    # Throttled to every 20th call — /detect fires 5x/sec and a disk write per
    # call adds needless I/O. Open static/results/last_ar_frame.jpg to inspect.
    global _ar_frame_counter
    _ar_frame_counter += 1
    if _ar_frame_counter % 20 == 1:
        try:
            os.makedirs("static/results", exist_ok=True)
            cv2.imwrite("static/results/last_ar_frame.jpg", frame)
        except Exception as e:
            log(f"[AR /detect] could not save debug frame: {e}")

    t0         = time.time()
    detections = detect_fruits_fast(frame, conf=AR_CONFIDENCE)
    # Largest first: Unity caps how many boxes it shows, so the cap keeps the
    # most prominent fruit, and track 0 (the analyzed one) stays consistent.
    detections.sort(key=lambda d: (d["bbox_norm"][2] - d["bbox_norm"][0]) *
                                  (d["bbox_norm"][3] - d["bbox_norm"][1]), reverse=True)
    elapsed_ms = round((time.time() - t0) * 1000, 1)
    log(f"[AR /detect] {len(detections)} fruit(s) in {elapsed_ms}ms "
        f"(frame {frame.shape[1]}x{frame.shape[0]})")

    if _rec_path:
        _rec_write(frame, detections)

    return jsonify({"detections": detections, "ts": time.time()})


@app.route('/ar-analyze', methods=['POST'])
def ar_analyze():
    """
    Deep pipeline — YOLO + OpenCV + Claude API. Target: ~1-2 seconds.
    Unity calls this every 3 seconds to update quality info panels.

    Accepts: multipart/form-data  {'image': <JPEG bytes>}
    Returns:
        {
            "detections": [...same format as /detect...],
            "analysis": [
                {"label": "apple", "quality": "Good",
                 "decay_stage": "Fresh", "days_remaining": 7,
                 "recommendation": "...", "confidence_ai": "High",
                 "defects": "None"}
            ],
            "ts": ...
        }
    """
    if not AR_ENABLED:
        return jsonify({"error": "AR disabled from dashboard",
                        "detections": [], "analysis": [], "ts": time.time()}), 503

    err = _require_ar_token()
    if err: return err

    frame, err = _decode_image()
    if err:
        return jsonify({"error": err, "detections": [], "analysis": [], "ts": time.time()}), 400

    # LIGHTWEIGHT AR PATH. The old version ran the full web pipeline
    # (detect_fruits = YOLO + GrabCut per fruit) plus ONE CLAUDE CALL PER FRUIT,
    # sequentially — several CPU-heavy seconds that starved the /detect requests
    # arriving every 200ms and stalled the AR boxes. AR needs neither GrabCut
    # contours nor quality info for every fruit at once, so now:
    #   - fast YOLO only (same as /detect)
    #   - Claude analyses ONLY the most prominent (largest) fruit
    #   - that fruit is moved to index 0 (Unity pairs analysis[i] with detections[i])
    detections = detect_fruits_fast(frame, conf=AR_CONFIDENCE)
    if not detections:
        return jsonify({"detections": [], "analysis": [], "ts": time.time()})

    h_img, w_img = frame.shape[:2]

    def _norm_area(d):
        x1, y1, x2, y2 = d["bbox_norm"]
        return (x2 - x1) * (y2 - y1)

    detections.sort(key=_norm_area, reverse=True)   # largest fruit first
    main = detections[0]

    # Crop the main fruit for Claude
    x1 = max(0, int(main["bbox_norm"][0] * w_img));  y1 = max(0, int(main["bbox_norm"][1] * h_img))
    x2 = min(w_img, int(main["bbox_norm"][2] * w_img));  y2 = min(h_img, int(main["bbox_norm"][3] * h_img))
    cropped = frame[y1:y2, x1:x2]

    analysis_out = []
    if cropped.size > 0:
        w_px, h_px = x2 - x1, y2 - y1
        size_info = {
            "width_px":  w_px,  "height_px": h_px,  "area_px": w_px * h_px,
            "width_cm":  main["width_cm"], "height_cm": main["height_cm"],
            "area_cm2":  round(main["width_cm"] * main["height_cm"], 1),
            "relative_size_percent": round(100 * (w_px * h_px) / (w_img * h_img), 2),
            "estimated_diameter_cm": round((main["width_cm"] + main["height_cm"]) / 2, 1),
        }
        # Downsample before k-means dominant colour — full-res crop is wasted work
        small = cv2.resize(cropped, (96, 96)) if min(cropped.shape[:2]) > 96 else cropped

        try:
            qa = analyse_fruit(cropped, size_info, get_dominant_color(small), main["label"])
        except Exception as e:
            log(f"[AR] Claude error: {e}")
            qa = {}

        item = {
            "label":          main["label"],
            "quality":        qa.get("QUALITY",        "?"),
            "decay_stage":    qa.get("DECAY_STAGE",    "?"),
            "days_remaining": qa.get("DAYS_REMAINING", "?"),
            "recommendation": qa.get("RECOMMENDATION", ""),
            "confidence_ai":  qa.get("CONFIDENCE",     "?"),
            "defects":        qa.get("DEFECTS",        "None"),
        }
        analysis_out.append(item)

        # Cache for the recorder/snapshot overlay so the saved media shows the
        # SAME quality panel the glasses display (it isn't in /detect frames).
        global _ar_last_qa, _ar_last_qa_ts
        _ar_last_qa, _ar_last_qa_ts = item, time.time()

        log(f"[AR] {main['label'].upper()}: {qa.get('QUALITY','?')} | "
            f"{qa.get('DECAY_STAGE','?')} | {qa.get('DAYS_REMAINING','?')} days")

        # NOTE: deliberately NOT persisted. /ar-analyze fires every ~3s while the
        # glasses run, so auto-saving here flooded scan_reports with throwaway
        # rows (1200+ of "noise") and wrecked the analytics. AR is live
        # inspection, not a logged scan. Use the dashboard /analyse for a saved
        # scan record (or the SNAP button for a shareable image).

    return jsonify({"detections": detections, "analysis": analysis_out, "ts": time.time()})


# ══════════════════════════════════════════════════════════
#  SETTINGS ROUTES
# ══════════════════════════════════════════════════════════

@app.route('/switch_camera', methods=['POST'])
def switch_camera():
    global camera, current_camera_index
    data  = request.get_json()
    index = int(data.get('index', 0))
    with camera_lock:
        if camera is not None:
            camera.release()
            camera = None
        current_camera_index = index
        try:
            camera = get_camera(index=current_camera_index)
            _save_config_key("camera_index", index)   # survives restarts
            log("📷 Switched to camera index {} (saved)".format(index))
            return jsonify({"status": "ok", "index": index})
        except Exception as e:
            log("✖ Camera switch failed: {}".format(e))
            return jsonify({"error": str(e)}), 500


@app.route('/stop_camera', methods=['POST'])
def stop_camera():
    global camera
    with camera_lock:
        if camera is not None:
            camera.release()
            camera = None
    log("📷 Camera stopped (upload mode)")
    return jsonify({"status": "stopped"})


@app.route('/start_camera', methods=['POST'])
def start_camera():
    global camera
    with camera_lock:
        camera = get_camera(index=current_camera_index)
    log("📷 Camera started (index {})".format(current_camera_index))
    return jsonify({"status": "started"})


# ── Device access management (Settings > Devices) ──────────
# Management itself is only permitted from devices that already have access
# (localhost always qualifies), so an unknown device can't approve itself.

def _device_admin_ok():
    c = request.remote_addr
    return (c in ("127.0.0.1", "::1") or c == LAN_IP
            or c in ALLOWED_IPS or not ENFORCE_IPS)


@app.route('/devices', methods=['GET'])
def list_devices():
    if not _device_admin_ok():
        return jsonify({"error": "Not authorised to manage devices."}), 403
    now = time.time()
    with _devices_lock:
        seen = {ip: dict(d) for ip, d in _seen_devices.items()}
    devices = []
    for ip in sorted(set(seen) | ALLOWED_IPS):
        d = seen.get(ip, {})
        devices.append({
            "ip":            ip,
            "allowed":       ip in ALLOWED_IPS,
            "blocked_count": d.get("blocked", 0),
            "last_seen_s":   round(now - d["last_seen"]) if d.get("last_seen") else None,
        })
    return jsonify({"enforce": ENFORCE_IPS, "devices": devices,
                    "your_ip": request.remote_addr})


@app.route('/devices/allow', methods=['POST'])
def allow_device():
    if not _device_admin_ok():
        return jsonify({"error": "Not authorised to manage devices."}), 403
    ip = (request.get_json() or {}).get('ip', '').strip()
    if not ip:
        return jsonify({"error": "No IP given"}), 400
    ALLOWED_IPS.add(ip)
    _save_config_key("allowed_ips", sorted(ALLOWED_IPS))
    log(f"🔓 Device allowed: {ip}")
    return jsonify({"status": "ok"})


@app.route('/devices/revoke', methods=['POST'])
def revoke_device():
    if not _device_admin_ok():
        return jsonify({"error": "Not authorised to manage devices."}), 403
    ip = (request.get_json() or {}).get('ip', '').strip()
    ALLOWED_IPS.discard(ip)
    _save_config_key("allowed_ips", sorted(ALLOWED_IPS))
    log(f"🔒 Device removed: {ip}")
    return jsonify({"status": "ok"})


@app.route('/devices/enforce', methods=['POST'])
def set_enforce():
    global ENFORCE_IPS
    if not _device_admin_ok():
        return jsonify({"error": "Not authorised to manage devices."}), 403
    ENFORCE_IPS = bool((request.get_json() or {}).get('on', False))
    _save_config_key("enforce_ips", ENFORCE_IPS)
    log(f"🔐 Device restriction {'ENABLED' if ENFORCE_IPS else 'disabled'}")
    return jsonify({"status": "ok", "enforce": ENFORCE_IPS})


@app.route('/set_hsv', methods=['POST'])
def set_hsv():
    data  = request.get_json()
    fruit = data.get('fruit')
    lower = data.get('lower')
    upper = data.get('upper')
    if fruit and lower and upper:
        hsv_overrides[fruit] = {"lower": lower, "upper": upper}
        log("🎨 HSV updated for {}: lower={} upper={}".format(fruit, lower, upper))
        return jsonify({"status": "ok"})
    return jsonify({"error": "Invalid data"}), 400


@app.route('/get_hsv', methods=['GET'])
def get_hsv():
    return jsonify(hsv_overrides)


@app.route('/set_pxcm', methods=['POST'])
def set_pxcm():
    data = request.get_json()
    val  = data.get('pixels_per_cm', 37)
    # Read-modify-write: a plain overwrite here used to wipe other config.json
    # keys (e.g. the persisted camera_index).
    _save_config_key("pixels_per_cm", val)
    log("📏 Pixels per cm set to {}".format(val))
    return jsonify({"status": "ok"})


@app.route('/history')
def history():
    """
    Returns scan history. Tries PostgreSQL first (survives restarts),
    falls back to in-memory if DB is unavailable.
    """
    try:
        with _pg() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id::text, session_id, scan_image, fruit_count,
                           results, mode, created_at::text
                    FROM   scan_reports
                    ORDER  BY created_at DESC
                    LIMIT  100
                    """
                )
                rows = cur.fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        log("⚠️  DB history unavailable ({}), using in-memory".format(e))
        return jsonify(scan_history)


@app.route('/clear', methods=['POST'])
def clear():
    """Clears in-memory history and current session's DB records."""
    scan_history.clear()
    try:
        with _pg() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM scan_reports WHERE session_id = %s", (SESSION_ID,)
                )
        log("🗑  History cleared (memory + DB session)")
    except Exception as e:
        log("🗑  History cleared (memory only — DB error: {})".format(e))
    return jsonify({"status": "cleared"})


# ══════════════════════════════════════════════════════════
#  RUN
# ══════════════════════════════════════════════════════════

HTTPS_PORT = 5443
CERT_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'certs')
CERT_FILE  = os.path.join(CERT_DIR, 'server.crt')
KEY_FILE   = os.path.join(CERT_DIR, 'server.key')


def _ensure_self_signed_cert():
    """
    Create (once) a self-signed cert so the phone can load /mobile over HTTPS —
    live getUserMedia camera preview only works on secure origins. Regenerated
    automatically if the LAN IP is no longer in the cert's SAN list.
    """
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime, ipaddress

    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        try:
            cert = x509.load_pem_x509_certificate(open(CERT_FILE, 'rb').read())
            sans = cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName).value
            if ipaddress.ip_address(LAN_IP) in sans.get_values_for_type(x509.IPAddress):
                return  # cert still matches our current LAN IP
        except Exception:
            pass  # unreadable/old cert — regenerate below

    os.makedirs(CERT_DIR, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Kiwi Sorter')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName('localhost'),
            x509.IPAddress(ipaddress.ip_address('127.0.0.1')),
            x509.IPAddress(ipaddress.ip_address(LAN_IP)),
        ]), critical=False)
        .sign(key, hashes.SHA256())
    )
    with open(KEY_FILE, 'wb') as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))
    with open(CERT_FILE, 'wb') as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    log(f"🔐 Generated self-signed cert for {LAN_IP} → certs/")


DISCOVERY_PORT = 5006


def _run_discovery():
    """
    UDP discovery beacon. The AR glasses app broadcasts a probe on whatever
    WiFi it is on; we answer with this laptop's current IP. Result: the Unity
    app never needs a hardcoded server address — it finds us on any network.
    Requires the firewall to allow UDP 5006 inbound (setup_admin.bat does this).
    """
    import socket as _s
    try:
        sock = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
        sock.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", DISCOVERY_PORT))
        log(f"📡 AR discovery listening on UDP {DISCOVERY_PORT}")
        while True:
            data, addr = sock.recvfrom(256)
            if data.strip() == b"KIWI_SORTER_DISCOVERY_V1":
                # Compute our IP FRESH per probe, routed toward the prober —
                # the startup LAN_IP goes stale when the laptop changes WiFi
                # while the server runs (it once replied with the OLD network's
                # IP, which the glasses couldn't reach).
                try:
                    probe_sock = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
                    probe_sock.connect((addr[0], 80))
                    my_ip = probe_sock.getsockname()[0]
                    probe_sock.close()
                except Exception:
                    my_ip = _get_lan_ip()
                reply = json.dumps({"app": "kiwi-sorter", "ip": my_ip,
                                    "port": 5000}).encode()
                sock.sendto(reply, addr)
                log(f"📡 AR device discovered us from {addr[0]} → told it {my_ip}")
    except Exception as e:
        log(f"📡 Discovery listener failed: {e}")


def _run_https():
    """Same Flask app on HTTPS for the phone's live camera (secure origin)."""
    try:
        _ensure_self_signed_cert()
        from werkzeug.serving import make_server
        srv = make_server('0.0.0.0', HTTPS_PORT, app, threaded=True,
                          ssl_context=(CERT_FILE, KEY_FILE))
        log(f"🔐 HTTPS listener : https://{LAN_IP}:{HTTPS_PORT}/mobile   ← live camera")
        srv.serve_forever()
    except Exception as e:
        log(f"🔐 HTTPS listener failed (photo mode still works on HTTP): {e}")


if __name__ == '__main__':
    log("🍎🍌🍊🥝 Fruit Sorter starting...")
    log(f"💻 Local browser : http://127.0.0.1:5000")
    log(f"📱 Phone scanner : http://{LAN_IP}:5000/mobile   ← open on your S24")
    log(f"🔍 AR endpoints  : POST /detect  |  POST /ar-analyze  (token required)")
    log(f"🔒 Security      : AR_TOKEN={'set' if AR_TOKEN else 'not set'} | "
        f"ALLOWED_IPS={'set ('+str(len(ALLOWED_IPS))+' devices)' if ALLOWED_IPS else 'disabled'}")
    # HTTPS twin on 5443: phones need a secure origin for live getUserMedia.
    # Unity/AR and the laptop dashboard stay on plain HTTP :5000.
    threading.Thread(target=_run_https, daemon=True).start()
    # UDP beacon so the AR glasses find this server on any WiFi (no hardcoded IP)
    threading.Thread(target=_run_discovery, daemon=True).start()
    # debug=False: Flask debug mode adds per-request overhead and runs the
    # auto-reloader (two processes). Hurts the 5 req/s AR pipeline.
    app.run(debug=False, threaded=True, host='0.0.0.0', port=5000)
