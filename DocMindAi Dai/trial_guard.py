# trial_guard.py — imported by app.py to enforce trial limits
import os
import json
import hashlib
from datetime import datetime, date, timedelta
from pathlib import Path

try:
    from trial_config import (
        TRIAL_EXPIRES, TRIAL_DAYS, MAX_QUERIES,
        CONTACT_NAME, CONTACT_EMAIL, CONTACT_PHONE,
        CONTACT_WEBSITE, CONTACT_MESSAGE, CUSTOMER_NAME
    )
except ImportError:
    # Fallback defaults if config missing
    TRIAL_EXPIRES = None
    TRIAL_DAYS    = 2
    MAX_QUERIES   = 10
    CONTACT_NAME  = "DocMind AI"
    CONTACT_EMAIL = "contact@docmindai.com"
    CONTACT_PHONE = ""
    CONTACT_WEBSITE = ""
    CONTACT_MESSAGE = "Your trial has ended. Please contact us to continue."
    CUSTOMER_NAME = "Customer"


# ── Resolve expiry date ──────────────────────────────────────
def _get_expiry() -> date:
    if TRIAL_EXPIRES:
        return datetime.strptime(TRIAL_EXPIRES, "%Y-%m-%d").date()
    days = TRIAL_DAYS or 2
    # Store first-run date in a hidden state file
    state = _load_state()
    if "first_run" not in state:
        state["first_run"] = date.today().isoformat()
        _save_state(state)
    first = datetime.strptime(state["first_run"], "%Y-%m-%d").date()
    return first + timedelta(days=days)


# ── State file (stores query count + first run) ──────────────
def _state_path() -> Path:
    # Store next to exe or in temp dir
    base = Path(os.environ.get("APPDATA", Path.home())) / ".docmindai"
    base.mkdir(parents=True, exist_ok=True)
    # Obscure the filename slightly
    key = hashlib.md5(CUSTOMER_NAME.encode()).hexdigest()[:8]
    return base / f".state_{key}"


def _load_state() -> dict:
    p = _state_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _save_state(state: dict):
    _state_path().write_text(json.dumps(state))


# ── Public API ───────────────────────────────────────────────
def check_trial() -> dict:
    """Returns dict with keys: valid(bool), reason(str), queries_left(int), expiry(str)"""
    state   = _load_state()
    expiry  = _get_expiry()
    today   = date.today()
    queries = state.get("queries", 0)

    if today > expiry:
        return {
            "valid": False,
            "reason": "expired",
            "queries_left": 0,
            "expiry": expiry.isoformat(),
        }

    if queries >= MAX_QUERIES:
        return {
            "valid": False,
            "reason": "limit_reached",
            "queries_left": 0,
            "expiry": expiry.isoformat(),
        }

    return {
        "valid": True,
        "reason": "ok",
        "queries_left": MAX_QUERIES - queries,
        "expiry": expiry.isoformat(),
    }


def record_query():
    """Call after each successful query to increment counter."""
    state = _load_state()
    state["queries"] = state.get("queries", 0) + 1
    _save_state(state)


def get_contact_info() -> dict:
    return {
        "name":    CONTACT_NAME,
        "email":   CONTACT_EMAIL,
        "phone":   CONTACT_PHONE,
        "website": CONTACT_WEBSITE,
        "message": CONTACT_MESSAGE,
        "customer": CUSTOMER_NAME,
    }


def get_trial_status() -> dict:
    """For the frontend status bar."""
    t = check_trial()
    expiry = _get_expiry()
    days_left = (expiry - date.today()).days
    return {
        "valid":       t["valid"],
        "queries_left": t["queries_left"],
        "days_left":   max(0, days_left),
        "expiry":      expiry.isoformat(),
        "max_queries": MAX_QUERIES,
    }
