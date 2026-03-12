#!/usr/bin/env python3
"""
Aviobook Cloud Server — Railway deployment
Fetches SimBrief OFP and serves it as HTML.
"""

import os
import sys
import json
import importlib.util
from flask import Flask, request, jsonify, send_from_directory, Response

# ── Load Aviobook module ──────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
AVIOBOOK_PY = os.path.join(SCRIPT_DIR, "Aviobook.py")

def _import_aviobook():
    spec = importlib.util.spec_from_file_location("aviobook", AVIOBOOK_PY)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

try:
    _av = _import_aviobook()
    print("  ✔  Aviobook.py loaded")
except Exception as e:
    print(f"  ✘  Could not load Aviobook.py: {e}")
    sys.exit(1)

# ── In-memory store (Railway ephemeral filesystem — no disk persistence) ──────
# Stores the last generated OFP HTML and flight archive list in memory.
_store = {
    "current_ofp": None,       # HTML string of latest OFP
    "archive": [],             # list of {id, orig, dest, flight, date, time, html}
    "next_id": 1,
}

STATIC_DIR = os.path.join(SCRIPT_DIR, "static")

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")

@app.route("/")
def index():
    """Serve the launcher UI."""
    try:
        with open(os.path.join(SCRIPT_DIR, "aviobook_launcher.html"), "r", encoding="utf-8") as f:
            html = f.read()
        # Inject archive data as JSON so the launcher can list past flights
        archive_json = json.dumps(_store["archive"])
        inject = f"<script>window.__ARCHIVE__ = {archive_json};</script>"
        html = html.replace("</head>", inject + "</head>")
        return Response(html, mimetype="text/html")
    except FileNotFoundError:
        return "aviobook_launcher.html not found", 404

@app.route("/ofp")
def serve_ofp():
    """Serve the current (latest) OFP, or redirect to launcher if none in memory."""
    if not _store["current_ofp"]:
        # PWA/fresh session — redirect gracefully instead of raw error
        return Response(
            "<html><head>"
            "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            "<meta http-equiv='refresh' content='0;url=/'>"
            "<style>body{background:#0d1f30;color:#4da8da;font-family:sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}</style>"
            "</head><body>"
            "<script>window.location.replace('/');</script>"
            "<p>Redirecting\u2026</p>"
            "</body></html>",
            mimetype="text/html"
        )
    return Response(_store["current_ofp"], mimetype="text/html")

@app.route("/ofp/<int:flight_id>")
def serve_archived_ofp(flight_id):
    """Serve a specific archived OFP by id."""
    for entry in _store["archive"]:
        if entry["id"] == flight_id:
            return Response(entry["html"], mimetype="text/html")
    return "Flight not found", 404

@app.route("/generate", methods=["POST"])
def generate():
    """Fetch SimBrief OFP and generate HTML."""
    try:
        req        = request.get_json(force=True)
        username   = (req.get("username") or "").strip()
        pilot_name = (req.get("pilot_name") or "").strip()

        if not username:
            return "username required", 400

        print(f"  ✈  Fetching OFP for: {username}")
        xml_data = _av.fetch_xml_from_api(username)
        data     = _av.parse_simbrief_xml(xml_data)

        if pilot_name and not data.get("ofp", {}).get("name"):
            if "ofp" not in data:
                data["ofp"] = {}
            data["ofp"]["name"] = pilot_name

        html = _av.generate_aviobook_html(data, pilot_name=pilot_name, release_folder=None)

        # Store as current
        _store["current_ofp"] = html

        # Archive entry
        from datetime import datetime, timezone
        g    = data.get("general", {})
        a    = data.get("airports", {})
        orig = a.get("origin", {}).get("icao", "???")
        dest = a.get("destination", {}).get("icao", "???")
        flt  = (g.get("icao_airline", "") + g.get("flight_number", "")).strip() or "FLT"

        try:
            ts   = int(data.get("times", {}).get("sched_off_ts") or 0)
            dt   = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)
        except Exception:
            dt = datetime.now(timezone.utc)

        entry = {
            "id":     _store["next_id"],
            "orig":   orig,
            "dest":   dest,
            "flight": flt,
            "date":   dt.strftime("%d %b %Y"),
            "time":   dt.strftime("%H:%M"),
            "html":   html,
        }
        # Avoid duplicate entries for same flight/time
        existing = next((e for e in _store["archive"]
                         if e["orig"] == orig and e["dest"] == dest
                         and e["flight"] == flt and e["time"] == entry["time"]), None)
        if not existing:
            _store["archive"].insert(0, entry)
            _store["next_id"] += 1

        print(f"  ✔  OFP generated ({len(html):,} bytes) — {orig}→{dest} {flt}")
        return jsonify({"ok": True, "ofp_url": "/ofp"})

    except Exception as e:
        import traceback
        print(f"  ✘  {traceback.format_exc()}")
        return f"Error: {e}", 500

@app.route("/archive")
def archive():
    """Return archive list as JSON (without HTML to keep payload small)."""
    slim = [{k: v for k, v in e.items() if k != "html"} for e in _store["archive"]]
    return jsonify(slim)

@app.route("/health")
def health():
    return "ok", 200

@app.route("/manifest.json")
def manifest():
    return send_from_directory(STATIC_DIR, "manifest.json")

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8742))
    print(f"\n  Aviobook Cloud Server running on port {port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
