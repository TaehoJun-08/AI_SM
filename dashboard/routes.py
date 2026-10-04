from flask import Blueprint, render_template, redirect, url_for, session
from helpers import current_user, login_required, role_required
from db import get_db
from matching import matches_for_sponsor, matches_for_projects


dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@dashboard_bp.route("/")
@login_required
def index():
    # Send each user to the dashboard that matches their role
    user = current_user()
    if user["role"] == "sponsor":
        return redirect(url_for("dashboard.sponsor"))
    return redirect(url_for("dashboard.innovator"))


@dashboard_bp.route("/innovator")
@login_required
@role_required("innovator")
def innovator():
    projects = get_db().execute(
        "SELECT * FROM projects WHERE innovator_id = ? ORDER BY id DESC",
        (session["user_id"],),
    ).fetchall()
    matches = matches_for_projects(projects)
    matched = sum(1 for m in matches.values() if m)
    interests = get_db().execute(
        """SELECT i.id, i.status, p.title, u.full_name, u.email, s.organisation
           FROM interests i
           JOIN projects p ON p.id = i.project_id
           JOIN users u ON u.id = i.sponsor_id
           LEFT JOIN sponsor_profiles s ON s.user_id = i.sponsor_id
           WHERE p.innovator_id = ? AND i.status != 'declined'
           ORDER BY i.status DESC, i.id DESC""",
        (session["user_id"],),
    ).fetchall()
    return render_template("dashboard/innovator.html", projects=projects,
                           matches=matches, matched=matched, interests=interests)
    
@dashboard_bp.route("/sponsor")
@login_required
@role_required("sponsor")
def sponsor():
    db = get_db()
    profile = db.execute(
        "SELECT * FROM sponsor_profiles WHERE user_id = ?", (session["user_id"],)
    ).fetchone()
    rows = db.execute(
        "SELECT domain FROM sponsor_domains WHERE user_id = ? ORDER BY domain",
        (session["user_id"],),
    ).fetchall()
    sent = {
        r["project_id"]: r
        for r in db.execute(
            """SELECT i.project_id, i.status, p.title, u.full_name, u.email
               FROM interests i
               JOIN projects p ON p.id = i.project_id
               JOIN users u ON u.id = p.innovator_id
               WHERE i.sponsor_id = ?
               ORDER BY i.id DESC""",
            (session["user_id"],),
        )
    }
    return render_template("dashboard/sponsor.html", profile=profile,
                           domains=[r["domain"] for r in rows],
                           matches=matches_for_sponsor(session["user_id"]),
                           sent=sent, connected=any(r["status"] == "accepted" for r in sent.values()))