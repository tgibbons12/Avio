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

# ── In-memory store ───────────────────────────────────────────────────────────
_store = {
    "current_ofp": None,
    "archive": [],
    "next_id": 1,
}

STATIC_DIR = os.path.join(SCRIPT_DIR, "static")

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")


def _build_launcher():
    """Build launcher HTML in memory — no file needed."""
    rows = ""
    for e in _store["archive"]:
        rows += (
            f"<a class='fl-row' href='/ofp/{e['id']}'>"
            f"<div class='fl-route'>{e['orig']} \u2192 {e['dest']} &nbsp; {e['flight']}</div>"
            f"<div class='fl-meta'>{e['date']} {e['time']}Z</div>"
            f"<div class='fl-chev'>\u203a</div>"
            f"</a>\n"
        )
    if not rows:
        rows = "<div style='padding:32px 20px;color:#4a7a96;font-size:13px;text-align:center;'>No flights yet \u2014 generate an OFP to begin.</div>"
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Aviobook</title>
<link rel="manifest" href="/manifest.json">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<style>
*{box-sizing:border-box;margin:0;padding:0;}
html{background:#0d3550;overscroll-behavior-y:none;}
body{background:linear-gradient(160deg,#13405a 0%,#1a4a61 50%,#163d55 100%);
  min-height:100vh;font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;
  overscroll-behavior-y:none;}
.header{background:rgba(0,0,0,0.3);padding:18px 20px 14px;
  border-bottom:1px solid rgba(90,174,239,0.18);display:flex;align-items:center;gap:12px;}
.header-logo{font-size:20px;font-weight:700;color:#7ad8fd;letter-spacing:1px;}
.header-sub{font-size:11px;color:#4a7a96;letter-spacing:.5px;margin-top:2px;}
.new-btn{margin-left:auto;background:linear-gradient(90deg,#1a6a9a,#1e7db8);border:none;
  border-radius:6px;color:#fff;font-size:12px;font-weight:700;letter-spacing:.5px;
  padding:9px 16px;cursor:pointer;text-transform:uppercase;text-decoration:none;display:inline-block;}
.section-title{padding:18px 20px 8px;font-size:11px;color:#4a7a96;letter-spacing:1px;text-transform:uppercase;}
.fl-row{display:flex;align-items:center;padding:14px 20px;
  border-bottom:1px solid rgba(90,174,239,0.1);text-decoration:none;cursor:pointer;gap:10px;}
.fl-row:active{background:rgba(90,174,239,0.08);}
.fl-route{flex:1;font-size:15px;font-weight:600;color:#e8f6ff;letter-spacing:.2px;}
.fl-meta{font-size:11px;color:#4a7a96;white-space:nowrap;}
.fl-chev{font-size:22px;color:#2a6a8b;line-height:1;}
</style>
</head>
<body>
<div class="header">
  <div>
    <div class="header-logo">AVIOBOOK</div>
    <div class="header-sub">FLIGHT ARCHIVE</div>
  </div>
  <a href="/ofp" class="new-btn">&#9654; Current Flight</a>
</div>
<div class="section-title">Past Flights</div>
""" + rows + """
</body>
</html>"""


@app.route("/")
def index():
    """Serve the launcher UI — built in memory, no file needed."""
    return Response(_build_launcher(), mimetype="text/html")


@app.route("/ofp")
def serve_ofp():
    """Serve the current OFP, or redirect to launcher if none."""
    if not _store["current_ofp"]:
        return Response(
            "<html><head>"
            "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
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
        _store["current_ofp"] = html

        from datetime import datetime, timezone
        g    = data.get("general", {})
        a    = data.get("airports", {})
        orig = a.get("origin", {}).get("icao", "???")
        dest = a.get("destination", {}).get("icao", "???")
        flt  = (g.get("icao_airline", "") + g.get("flight_number", "")).strip() or "FLT"

        try:
            ts = int(data.get("times", {}).get("sched_off_ts") or 0)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)
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
