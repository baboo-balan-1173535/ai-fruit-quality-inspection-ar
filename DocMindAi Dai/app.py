# app.py — DocMind AI v2.0
# Changes from v1:
#   - Trial system (trial_guard, trial_config) completely removed
#   - Flask session used for session_id — persists across page refreshes
#   - Port changed to 5001 — avoids conflict with kiwi sorter (port 5000)
#   - /clear-history, /history, /status endpoints added
#   - Error messages no longer leak internal details to client

import os
import sys
import uuid
import threading
import webbrowser
from dotenv import load_dotenv

load_dotenv()

import json as _json
from flask import Flask, request, jsonify, render_template, session, Response
from werkzeug.utils import secure_filename
from rag_engine import RAGEngine


# ── App setup ──────────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["UPLOAD_FOLDER"]      = "uploads"
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024   # 32 MB limit

# FLASK_SECRET_KEY must be set in .env for sessions to survive app restarts.
# If not set, a random key is used (sessions reset on restart — fine for dev).
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24).hex())

os.makedirs("uploads", exist_ok=True)
engine       = RAGEngine()
ALLOWED_EXTS = {"pdf", "docx", "txt"}


# ── Helpers ────────────────────────────────────────────────────────────────────

def allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTS


def get_sid() -> str:
    """Return the session ID for this browser session, creating one if needed."""
    if "sid" not in session:
        session["sid"] = str(uuid.uuid4())
    return session["sid"]


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    get_sid()   # ensure session ID is created on first visit
    return render_template("index.html", web_available=engine.get_web_available())


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400
    if not allowed(file.filename):
        return jsonify({"error": "Unsupported file type. Use PDF, DOCX or TXT."}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    try:
        result = engine.load_and_index(filepath, filename)
        already = result.get("already_indexed", False)
        msg = "already indexed — loaded from database." if already else "indexed and ready."
        print(f"[UPLOAD] '{filename}' {msg}")
        return jsonify({"success": True, **result})
    except Exception as e:
        print(f"[UPLOAD ERROR] {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/ask", methods=["POST"])
def ask():
    data     = request.get_json() or {}
    question = data.get("question", "").strip()
    use_web  = bool(data.get("use_web", False))

    if not question:
        return jsonify({"error": "No question provided"}), 400

    if not use_web and not engine.is_ready():
        return jsonify({
            "error": "Upload a document first, or enable web search 🌐"
        }), 400

    doc_ids = data.get("doc_ids") or None   # Library scope (list of UUID strings)

    try:
        result = engine.ask(question, session_id=get_sid(), use_web=use_web,
                            doc_ids=doc_ids)
        return jsonify(result)
    except Exception as e:
        print(f"[ASK ERROR] {e}")
        return jsonify({"error": "Something went wrong. Please try again."}), 500


@app.route("/ask-stream", methods=["POST"])
def ask_stream():
    """Streaming /ask — server-sent events: {type:delta,text} ... {type:done,...}."""
    data     = request.get_json() or {}
    question = data.get("question", "").strip()
    use_web  = bool(data.get("use_web", False))
    doc_ids  = data.get("doc_ids") or None

    if not question:
        return jsonify({"error": "No question provided"}), 400
    if not use_web and not engine.is_ready():
        return jsonify({"error": "Upload a document first, or enable web search 🌐"}), 400

    sid = get_sid()   # resolve inside request context, before the generator runs

    def events():
        try:
            for kind, payload in engine.ask_stream(question, session_id=sid,
                                                   use_web=use_web, doc_ids=doc_ids):
                if kind == "delta":
                    yield f"data: {_json.dumps({'type': 'delta', 'text': payload})}\n\n"
                else:
                    yield f"data: {_json.dumps({'type': 'done', **payload})}\n\n"
        except Exception as e:
            print(f"[ASK-STREAM ERROR] {e}")
            yield f"data: {_json.dumps({'type': 'error', 'error': 'Something went wrong.'})}\n\n"

    return Response(events(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/documents", methods=["GET"])
def documents():
    """Library panel: all indexed documents."""
    return jsonify({"documents": engine.list_documents()})


@app.route("/documents/<doc_id>/delete", methods=["POST"])
def delete_document(doc_id):
    """Remove a document from FAISS + the registry."""
    try:
        return jsonify(engine.delete_document(doc_id))
    except Exception as e:
        print(f"[DELETE-DOC ERROR] {e}")
        return jsonify({"error": "Could not delete document."}), 500


@app.route("/analytics", methods=["GET"])
def analytics():
    """SQL-aggregated scan statistics for the Analytics panel."""
    try:
        return jsonify(engine.get_analytics())
    except Exception as e:
        print(f"[ANALYTICS ERROR] {e}")
        return jsonify({"error": "Could not compute analytics (DB offline?)"}), 500


@app.route("/load-scans", methods=["POST"])
def load_scans():
    """
    Pull all scan_reports from the kiwi_sorter PostgreSQL database,
    convert them to text documents, and add them to the FAISS index.
    The chatbot can then answer questions about past fruit scans.
    """
    try:
        result = engine.load_scan_reports()
        return jsonify({"success": True, **result})
    except Exception as e:
        print(f"[LOAD-SCANS ERROR] {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/clear-history", methods=["POST"])
def clear_history():
    """Wipe conversation history for the current browser session."""
    engine.clear_history(get_sid())
    return jsonify({"success": True})


@app.route("/history", methods=["GET"])
def history():
    """Return the last 50 messages for the current session (for debugging)."""
    msgs = engine.load_history(get_sid(), limit=50)
    return jsonify({"messages": msgs})


@app.route("/status", methods=["GET"])
def status():
    """Health-check endpoint — used by the frontend to show connection status."""
    return jsonify({
        "doc_ready":     engine.is_ready(),
        "web_available": engine.get_web_available(),
        "db_connected":  engine.db_connected(),
    })


# ── Entry point ────────────────────────────────────────────────────────────────

def _open_browser():
    import time
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:5001")


if __name__ == "__main__":
    # When packaged as .exe — auto-open browser
    if getattr(sys, "frozen", False):
        threading.Thread(target=_open_browser, daemon=True).start()

    print("[DocMind AI] Starting on http://127.0.0.1:5001  (and LAN IP)")
    print("[DocMind AI] Note: kiwi sorter runs on port 5000 — no conflict.")
    # 0.0.0.0: also reachable via the laptop's LAN IP — the dashboard's
    # "Reports & Chat" button and phones open it by LAN address.
    app.run(host="0.0.0.0", port=5001, debug=False)
