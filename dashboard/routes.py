from flask import Blueprint, render_template, redirect, url_for
from helpers import current_user, login_required, role_required

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
    return render_template("dashboard/innovator.html")


@dashboard_bp.route("/sponsor")
@login_required
@role_required("sponsor")
def sponsor():
    return render_template("dashboard/sponsor.html")