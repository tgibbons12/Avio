#!/usr/bin/env python3
"""
Aviobook Cloud Server — Railway deployment
"""

import os
import sys
import json
import importlib.util
from flask import Flask, request, jsonify, send_from_directory, Response

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
AVIOBOOK_PY = os.path.join(SCRIPT_DIR, "Aviobook.py")

def _import_aviobook():
    spec = importlib.util.spec_from_file_location("aviobook", AVIOBOOK_PY)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

try:
    _av = _import_aviobook()
    print("  \u2714  Aviobook.py loaded")
except Exception as e:
    print(f"  \u2718  Could not load Aviobook.py: {e}")
    sys.exit(1)

_store = {
    "current_ofp": None,
    "archive": [],
    "next_id": 1,
}

STATIC_DIR = os.path.join(SCRIPT_DIR, "static")
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")


def _build_launcher():
    rows = ""
    for e in _store["archive"]:
        rows += (
            "<a class='fl-row' href='/ofp/" + str(e["id"]) + "'>"
            "<div class='fl-route'>" + e["orig"] + " \u2192 " + e["dest"] + " &nbsp; " + e["flight"] + "</div>"
            "<div class='fl-meta'>" + e["date"] + " " + e["time"] + "Z</div>"
            "<div class='fl-chev'>\u203a</div>"
            "</a>\n"
        )
    if not rows:
        rows = "<div class='empty'>No flights yet \u2014 load an OFP to begin.</div>"

    has_current = "true" if _store["current_ofp"] else "false"

    return (
        "<!DOCTYPE html>\n"
        "<html lang='en'>\n"
        "<head>\n"
        "<meta charset='UTF-8'>\n"
        "<meta name='viewport' content='width=device-width,initial-scale=1,maximum-scale=1,viewport-fit=cover'>\n"
        "<title>Aviobook</title>\n"
        "<link rel='manifest' href='/manifest.json'>\n"
        "<meta name='apple-mobile-web-app-capable' content='yes'>\n"
        "<meta name='apple-mobile-web-app-status-bar-style' content='black-translucent'>\n"
        "<style>\n"
        "*{box-sizing:border-box;margin:0;padding:0;}\n"
        "html{background:#0d3550;overscroll-behavior-y:none;}\n"
        "body{background:linear-gradient(160deg,#13405a 0%,#1a4a61 50%,#163d55 100%);\n"
        "  min-height:100vh;font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;\n"
        "  overscroll-behavior-y:none;color:#eaf6ff;}\n"
        ".header{background:rgba(0,0,0,0.3);padding:18px 20px 14px;\n"
        "  border-bottom:1px solid rgba(90,174,239,0.18);display:flex;align-items:center;gap:12px;}\n"
        ".header-logo{font-size:20px;font-weight:700;color:#7ad8fd;letter-spacing:1px;}\n"
        ".header-sub{font-size:11px;color:#4a7a96;letter-spacing:.5px;margin-top:2px;}\n"
        ".signout-btn{margin-left:auto;background:transparent;border:1px solid rgba(90,174,239,0.3);\n"
        "  border-radius:6px;color:#4a8aa8;font-size:11px;font-weight:700;letter-spacing:.5px;\n"
        "  padding:7px 14px;cursor:pointer;text-transform:uppercase;font-family:inherit;}\n"
        ".signout-btn:active{background:rgba(90,174,239,0.1);}\n"
        ".login-wrap{display:flex;align-items:center;justify-content:center;\n"
        "  padding:40px 20px;min-height:60vh;}\n"
        ".login-card{background:linear-gradient(160deg,#1a4a61 0%,#21546D 60%,#1c4a60 100%);\n"
        "  border:1px solid rgba(90,174,239,0.2);border-radius:12px;padding:32px 28px;\n"
        "  width:100%;max-width:360px;box-shadow:0 8px 40px rgba(0,0,0,0.5);}\n"
        ".login-title{font-size:16px;font-weight:700;color:#7ad8fd;letter-spacing:.5px;\n"
        "  text-transform:uppercase;margin-bottom:6px;}\n"
        ".login-sub{font-size:12px;color:#4a8aa8;margin-bottom:24px;}\n"
        ".login-label{display:block;font-size:11px;color:#6ab4d4;text-transform:uppercase;\n"
        "  letter-spacing:.8px;margin-bottom:6px;}\n"
        ".login-input{width:100%;background:rgba(0,0,0,0.3);border:1px solid rgba(90,174,239,0.25);\n"
        "  border-radius:6px;padding:12px 14px;color:#eaf6ff;font-size:15px;\n"
        "  font-family:inherit;outline:none;transition:border-color .2s;}\n"
        ".login-input:focus{border-color:rgba(90,174,239,0.6);}\n"
        ".login-input.error{border-color:rgba(220,80,80,0.7);}\n"
        ".login-input::placeholder{color:#2a5a78;}\n"
        ".remember-row{display:flex;align-items:center;gap:10px;margin:16px 0 24px;cursor:pointer;}\n"
        ".remember-toggle{width:40px;height:24px;background:rgba(0,0,0,0.4);border-radius:12px;\n"
        "  position:relative;transition:background .2s;flex-shrink:0;border:1px solid rgba(90,174,239,0.2);}\n"
        ".remember-toggle.on{background:rgba(74,205,130,0.35);border-color:rgba(74,205,130,0.5);}\n"
        ".remember-toggle::after{content:'';position:absolute;top:3px;left:3px;width:16px;height:16px;\n"
        "  background:#4a7a96;border-radius:50%;transition:left .2s,background .2s;}\n"
        ".remember-toggle.on::after{left:19px;background:#4cdf8a;}\n"
        ".remember-label{font-size:13px;color:#6ab4d4;}\n"
        ".login-btn{width:100%;background:linear-gradient(90deg,#1a6a9a,#1e7db8);border:none;\n"
        "  border-radius:6px;color:#fff;font-size:14px;font-weight:700;letter-spacing:.5px;\n"
        "  padding:14px;cursor:pointer;text-transform:uppercase;font-family:inherit;transition:opacity .2s;}\n"
        ".login-btn:active{opacity:.8;}\n"
        ".login-btn:disabled{opacity:.4;cursor:not-allowed;}\n"
        ".login-error{color:#e07070;font-size:12px;margin-top:12px;text-align:center;display:none;}\n"
        ".login-spinner{display:none;text-align:center;color:#4a8aa8;font-size:13px;margin-top:16px;}\n"
        ".section-title{padding:18px 20px 8px;font-size:11px;color:#4a7a96;\n"
        "  letter-spacing:1px;text-transform:uppercase;}\n"
        ".fl-row{display:flex;align-items:center;padding:14px 20px;\n"
        "  border-bottom:1px solid rgba(90,174,239,0.1);text-decoration:none;gap:10px;}\n"
        ".fl-row:active{background:rgba(90,174,239,0.08);}\n"
        ".fl-route{flex:1;font-size:15px;font-weight:600;color:#e8f6ff;letter-spacing:.2px;}\n"
        ".fl-meta{font-size:11px;color:#4a7a96;white-space:nowrap;}\n"
        ".fl-chev{font-size:22px;color:#2a6a8b;line-height:1;}\n"
        ".empty{padding:32px 20px;color:#4a7a96;font-size:13px;text-align:center;}\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        "<div class='header'>\n"
        "  <div>\n"
        "    <div class='header-logo'>AVIOBOOK</div>\n"
        "    <div class='header-sub' id='header-user'>FLIGHT PLANNING</div>\n"
        "  </div>\n"
        "  <button class='signout-btn' id='signout-btn' style='display:none' onclick='signOut()'>Sign Out</button>\n"
        "</div>\n"
        "<div class='login-wrap' id='login-wrap'>\n"
        "  <div class='login-card'>\n"
        "    <div class='login-title'>Load Flight Plan</div>\n"
        "    <div class='login-sub'>Enter your SimBrief username to load your latest OFP</div>\n"
        "    <label class='login-label' for='sb-username'>SimBrief Username</label>\n"
        "    <input class='login-input' id='sb-username' type='text'\n"
        "           autocomplete='off' autocorrect='off' autocapitalize='off'\n"
        "           spellcheck='false' placeholder='e.g. tgibbons'>\n"
        "    <div class='remember-row' onclick='toggleRemember()'>\n"
        "      <div class='remember-toggle' id='remember-toggle'></div>\n"
        "      <span class='remember-label'>Remember me on this device</span>\n"
        "    </div>\n"
        "    <button class='login-btn' id='login-btn' onclick='doLoad()'>Load OFP</button>\n"
        "    <div class='login-error' id='login-error'>Username not found or no active flight plan.</div>\n"
        "    <div class='login-spinner' id='login-spinner'>Loading flight plan\u2026</div>\n"
        "  </div>\n"
        "</div>\n"
        "<div id='archive-section' style='display:none'>\n"
        "  <div class='section-title'>Past Flights</div>\n"
        + rows +
        "</div>\n"
        "<script>\n"
        "var _remember = false;\n"
        "var HAS_CURRENT = " + has_current + ";\n"
        "\n"
        "function init() {\n"
        "  var saved = localStorage.getItem('av_username');\n"
        "  if (saved) {\n"
        "    document.getElementById('sb-username').value = saved;\n"
        "    _remember = true;\n"
        "    document.getElementById('remember-toggle').classList.add('on');\n"
        "    document.getElementById('header-user').textContent = saved.toUpperCase();\n"
        "    document.getElementById('signout-btn').style.display = '';\n"
        "  }\n"
        "  var arc = document.getElementById('archive-section');\n"
        "  if (arc && arc.querySelectorAll('.fl-row').length > 0) arc.style.display = 'block';\n"
        "  if (saved && HAS_CURRENT) { window.location.href = '/ofp'; return; }\n"
        "  if (saved) doLoad();\n"
        "}\n"
        "\n"
        "function toggleRemember() {\n"
        "  _remember = !_remember;\n"
        "  document.getElementById('remember-toggle').classList.toggle('on', _remember);\n"
        "}\n"
        "\n"
        "function signOut() {\n"
        "  localStorage.removeItem('av_username');\n"
        "  document.getElementById('sb-username').value = '';\n"
        "  _remember = false;\n"
        "  document.getElementById('remember-toggle').classList.remove('on');\n"
        "  document.getElementById('signout-btn').style.display = 'none';\n"
        "  document.getElementById('header-user').textContent = 'FLIGHT PLANNING';\n"
        "}\n"
        "\n"
        "function doLoad() {\n"
        "  var username = document.getElementById('sb-username').value.trim();\n"
        "  if (!username) {\n"
        "    var inp = document.getElementById('sb-username');\n"
        "    inp.classList.add('error');\n"
        "    setTimeout(function(){ inp.classList.remove('error'); }, 2000);\n"
        "    return;\n"
        "  }\n"
        "  if (_remember) { localStorage.setItem('av_username', username); }\n"
        "  else { localStorage.removeItem('av_username'); }\n"
        "  var btn = document.getElementById('login-btn');\n"
        "  var spinner = document.getElementById('login-spinner');\n"
        "  var errEl = document.getElementById('login-error');\n"
        "  btn.disabled = true;\n"
        "  spinner.style.display = 'block';\n"
        "  errEl.style.display = 'none';\n"
        "  fetch('/generate', {\n"
        "    method: 'POST',\n"
        "    headers: {'Content-Type': 'application/json'},\n"
        "    body: JSON.stringify({ username: username })\n"
        "  })\n"
        "  .then(function(r) { if (!r.ok) throw new Error('Failed'); return r.json(); })\n"
        "  .then(function() { window.location.href = '/ofp'; })\n"
        "  .catch(function() {\n"
        "    btn.disabled = false;\n"
        "    spinner.style.display = 'none';\n"
        "    errEl.style.display = 'block';\n"
        "  });\n"
        "}\n"
        "\n"
        "document.getElementById('sb-username').addEventListener('keydown', function(e) {\n"
        "  if (e.key === 'Enter') doLoad();\n"
        "});\n"
        "\n"
        "init();\n"
        "</script>\n"
        "</body>\n"
        "</html>\n"
    )


@app.route("/")
def index():
    return Response(_build_launcher(), mimetype="text/html")


@app.route("/ofp")
def serve_ofp():
    if not _store["current_ofp"]:
        return Response(
            "<html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<style>body{background:#0d1f30;color:#4da8da;font-family:sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}</style>"
            "</head><body><script>window.location.replace('/');</script>"
            "<p>Redirecting\u2026</p></body></html>",
            mimetype="text/html"
        )
    return Response(_store["current_ofp"], mimetype="text/html")


@app.route("/ofp/<int:flight_id>")
def serve_archived_ofp(flight_id):
    for entry in _store["archive"]:
        if entry["id"] == flight_id:
            return Response(entry["html"], mimetype="text/html")
    return "Flight not found", 404


@app.route("/generate", methods=["POST"])
def generate():
    try:
        req        = request.get_json(force=True)
        username   = (req.get("username") or "").strip()
        pilot_name = (req.get("pilot_name") or "").strip()

        if not username:
            return "username required", 400

        print(f"  \u2708  Fetching OFP for: {username}")
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

        print(f"  \u2714  OFP generated ({len(html):,} bytes) \u2014 {orig}\u2192{dest} {flt}")
        return jsonify({"ok": True, "ofp_url": "/ofp"})

    except Exception as e:
        import traceback
        print(f"  \u2718  {traceback.format_exc()}")
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8742))
    print(f"\n  Aviobook Cloud Server running on port {port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
