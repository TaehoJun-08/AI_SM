from flask import Blueprint, render_template, redirect, url_for, session
from helpers import current_user, login_required, role_required
from db import get_db


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
    return render_template("dashboard/innovator.html", projects=projects)

@dashboard_bp.route("/sponsor")
@login_required
@role_required("sponsor")
def sponsor():
    return render_template("dashboard/sponsor.html")