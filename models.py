"""
Persistence layer — one row per pilot, everything else scoped off it.

Ported from MobileCCI, which replaced this same module-level-global-dict
design with real per-user rows. The bug that forced it there is the one
this fixes here: a single `_store` shared by every visitor meant every
pilot's flight list, and every archived OFP, was readable by anyone who
loaded the page.

The generated OFP HTML rides in a Text column, matching MobileCCI's
ReleaseCache: it keeps this to one table with no filesystem dependency,
which matters on Railway where the app's own disk is ephemeral.
"""
from datetime import datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


def _now():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    # The SimBrief account this pilot pulls OFPs from. Replaces the old
    # localStorage 'av_username' key so it follows them across devices
    # instead of living on one browser.
    simbrief_user = db.Column(db.String(64))
    # Which flight this pilot is currently viewing — the persistent
    # replacement for session["ofp_id"], which was lost on every restart
    # because SECRET_KEY defaulted to os.urandom(32).
    current_flight_id = db.Column(db.Integer)
    last_seen = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=_now)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Flight(db.Model):
    """One generated OFP, owned by one pilot.

    `html` is the fully rendered Aviobook page. Storing it rather than
    re-rendering follows MobileCCI's ReleaseCache reasoning: generation is
    a live SimBrief fetch plus a full render, far too slow to repeat on
    every view of a page the pilot may open many times.
    """
    __tablename__ = "flights"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    orig = db.Column(db.String(8))
    dest = db.Column(db.String(8))
    flight_no = db.Column(db.String(16))
    sched_date = db.Column(db.String(24))
    sched_time = db.Column(db.String(8))
    html = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=_now, index=True)
