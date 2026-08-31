#!/usr/bin/env python3
"""
Aviobook Cloud Server — Railway deployment.

Backend ported from MobileCCI. The change that matters: this app used to
keep everything in one module-level `_store` dict, which meant a single
shared archive readable by every visitor, wiped on every restart, and only
coherent because gunicorn happened to run a single worker. State now lives
in Postgres, scoped to a logged-in pilot.
"""

import logging
import os
import sys
import importlib.util
from datetime import datetime, timedelta, timezone

from flask import (Flask, request, jsonify, send_from_directory, Response,
                   render_template, redirect, url_for, abort)
from flask_login import (LoginManager, login_user, logout_user, login_required,
                         current_user)

from models import db, User, Flight

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
LOG = logging.getLogger("aviobook")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(SCRIPT_DIR, "static")
AVIOBOOK_PY = os.path.join(SCRIPT_DIR, "Aviobook.py")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")

# A random fallback key silently signs every session out on restart, and
# hands each gunicorn worker a different key. The old code accepted that
# quietly; this refuses to start without a real one in production, because
# the failure it causes looks like data loss rather than a config mistake.
_secret = os.environ.get("SECRET_KEY")
if not _secret:
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("DATABASE_URL"):
        LOG.error("SECRET_KEY is not set. Set it in the Railway dashboard.")
        raise SystemExit(1)
    LOG.warning("SECRET_KEY not set — using an insecure dev default.")
    _secret = "dev-insecure-key"
app.secret_key = _secret

# Railway hands out postgres:// but SQLAlchemy 2 wants postgresql://.
_db_url = os.environ.get("DATABASE_URL", "")
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _db_url or f"sqlite:///{SCRIPT_DIR}/aviobook.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"


@login_manager.user_loader
def _load_user(uid):
    return db.session.get(User, int(uid))


# --- Aviobook.py is imported lazily -----------------------------------
# It used to be imported at module scope with sys.exit(1) on failure, which
# under gunicorn kills the worker before it can serve anything — Railway
# then retries and gives up, leaving the service down with the reason only
# in build logs. Now a broken renderer fails one request, loudly, and the
# rest of the app (including /health) stays up.
_av = None


def _aviobook():
    global _av
    if _av is None:
        spec = importlib.util.spec_from_file_location("aviobook", AVIOBOOK_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _av = mod
        LOG.info("Aviobook.py loaded")
    return _av


def _ensure_columns():
    """Bare-bones migration for columns added after first deploy —
    create_all() only creates missing tables, it never alters an existing
    one. Same approach MobileCCI uses; Alembic would replace it if the
    schema ever starts moving faster than this."""
    from sqlalchemy import inspect, text as sa_text
    inspector = inspect(db.engine)
    if "users" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("users")}
    for col, ddl in (("simbrief_user", "VARCHAR(64)"),
                     ("current_flight_id", "INTEGER"),
                     ("last_seen", "TIMESTAMP")):
        if col not in existing:
            with db.engine.begin() as conn:
                conn.execute(sa_text(f"ALTER TABLE users ADD COLUMN {col} {ddl}"))
            LOG.info("Migrated: added users.%s", col)


with app.app_context():
    db.create_all()
    _ensure_columns()


_LAST_SEEN_THROTTLE = timedelta(minutes=5)


@app.before_request
def _touch_last_seen():
    """Wrapped so a write failure can never sink the request it rode in
    on — 'last active' only needs to be roughly right."""
    try:
        if not current_user.is_authenticated:
            return
        now = datetime.now(timezone.utc)
        seen = current_user.last_seen
        if seen is not None and seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        if seen is None or (now - seen) > _LAST_SEEN_THROTTLE:
            current_user.last_seen = now
            db.session.commit()
    except Exception:
        db.session.rollback()


# --- auth --------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    error = username = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user, remember=True)
            return redirect(url_for("index"))
        # Deliberately does not say which half was wrong.
        error = "That username and password don't match."
    return render_template("auth.html", register=False, error=error, username=username)


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    error = username = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if len(username) < 3:
            error = "Username must be at least 3 characters."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif User.query.filter_by(username=username).first():
            error = "That username is taken."
        else:
            user = User(username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user, remember=True)
            return redirect(url_for("index"))
    return render_template("auth.html", register=True, error=error, username=username)


@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("login"))


# --- flights -----------------------------------------------------------

def _home_button(html, href="/"):
    btn = (
        "<div style=\"position:fixed;top:calc(20px + env(safe-area-inset-top));right:20px;"
        "z-index:10000;\"><a href=\"" + href + "\" style=\"background:#1a6a9a;color:#fff;"
        "border-radius:6px;padding:10px 16px;font-weight:700;font-size:12px;letter-spacing:.5px;"
        "text-transform:uppercase;font-family:-apple-system,sans-serif;text-decoration:none;"
        "display:inline-block;\">&larr; Home</a></div>"
    )
    return html.replace("</body>", btn + "</body>") if "</body>" in html else html + btn


@app.route("/")
@login_required
def index():
    flights = (Flight.query.filter_by(user_id=current_user.id)
               .order_by(Flight.created_at.desc()).all())
    return render_template("launcher.html", flights=flights)


@app.route("/ofp")
@login_required
def serve_ofp():
    fid = current_user.current_flight_id
    if fid:
        return redirect(url_for("serve_flight", flight_id=fid))
    return redirect(url_for("index"))


@app.route("/ofp/<int:flight_id>")
@app.route("/flight/<int:flight_id>")
@login_required
def serve_flight(flight_id):
    # Scoped to the owner. The old route looped a global archive with no
    # ownership check at all, so /ofp/1, /ofp/2 ... walked every pilot's
    # flight plans.
    f = Flight.query.filter_by(id=flight_id, user_id=current_user.id).first()
    if not f:
        abort(404)
    return Response(_home_button(f.html), mimetype="text/html")


@app.route("/flight/<int:flight_id>", methods=["DELETE"])
@login_required
def delete_flight(flight_id):
    f = Flight.query.filter_by(id=flight_id, user_id=current_user.id).first()
    if not f:
        abort(404)
    if current_user.current_flight_id == f.id:
        current_user.current_flight_id = None
    db.session.delete(f)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/generate", methods=["POST"])
@login_required
def generate():
    req = request.get_json(silent=True) or {}
    username = (req.get("username") or "").strip()
    if not username:
        return jsonify({"error": "SimBrief username required."}), 400

    try:
        av = _aviobook()
        xml_data = av.fetch_xml_from_api(username)
        data = av.parse_simbrief_xml(xml_data)
        html = av.generate_aviobook_html(data, pilot_name=current_user.username,
                                         release_folder=None)
    except Exception:
        # The traceback goes to the server log, never to the client. The
        # old code returned it in the response body and also served the
        # last one from an unauthenticated /debug route.
        LOG.exception("OFP generation failed for SimBrief user %r", username)
        return jsonify({"error": "Could not load that flight plan. Check the "
                                 "SimBrief username and that it has a current OFP."}), 502

    g = data.get("general", {}) or {}
    a = data.get("airports", {}) or {}
    orig = (a.get("origin", {}) or {}).get("icao") or "----"
    dest = (a.get("destination", {}) or {}).get("icao") or "----"
    flt = ((g.get("icao_airline") or "") + (g.get("flight_number") or "")).strip() or "FLT"

    try:
        ts = int((data.get("times", {}) or {}).get("sched_off_ts") or 0)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)
    except (TypeError, ValueError):
        dt = datetime.now(timezone.utc)

    sched_date, sched_time = dt.strftime("%d %b %Y"), dt.strftime("%H:%M")

    # Re-loading the same OFP refreshes it in place rather than stacking
    # duplicates, matching the old dedupe but scoped to this pilot.
    f = Flight.query.filter_by(user_id=current_user.id, orig=orig, dest=dest,
                               flight_no=flt, sched_time=sched_time).first()
    if f:
        f.html = html
    else:
        f = Flight(user_id=current_user.id, orig=orig, dest=dest, flight_no=flt,
                   sched_date=sched_date, sched_time=sched_time, html=html)
        db.session.add(f)
        db.session.flush()

    current_user.current_flight_id = f.id
    if username != current_user.simbrief_user:
        current_user.simbrief_user = username
    db.session.commit()

    LOG.info("OFP generated for %s: %s->%s %s (%d bytes)",
             current_user.username, orig, dest, flt, len(html))
    return jsonify({"ok": True, "ofp_url": url_for("serve_flight", flight_id=f.id)})


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "dev")[:7]})


@app.route("/manifest.json")
def manifest():
    return send_from_directory(STATIC_DIR, "manifest.json")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8742))
    LOG.info("Aviobook Cloud Server on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
