from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, session)
from db import get_db
from helpers import login_required, role_required

projects_bp = Blueprint("projects", __name__, url_prefix="/projects")

DOMAINS = ["AI / ML", "HealthTech", "EdTech", "FinTech", "AgriTech",
           "CleanTech", "Social Impact", "Other"]
STAGES = {"idea": "Idea", "prototype": "Prototype", "mvp": "MVP", "growth": "Growth"}


def show_form(form=None):
    return render_template("projects/new.html",
                           domains=DOMAINS, stages=STAGES, form=form or {})


@projects_bp.route("/new", methods=["GET", "POST"])
@login_required
@role_required("innovator")
def new():
    if request.method == "GET":
        return show_form()

    title = request.form.get("title", "").strip()
    summary = request.form.get("summary", "").strip()
    description = request.form.get("description", "").strip()
    domain = request.form.get("domain", "")
    stage = request.form.get("stage", "")
    funding = request.form.get("funding_needed", "").strip()

    if not all([title, summary, description, domain, stage, funding]):
        flash("Please fill in every field.")
        return show_form(request.form)

    if domain not in DOMAINS or stage not in STAGES:
        flash("Please choose a valid domain and stage.")
        return show_form(request.form)

    if not funding.isdigit() or int(funding) <= 0:
        flash("Funding needed must be a whole number above 0.")
        return show_form(request.form)

    if len(summary) > 160:
        flash("Keep the summary to 160 characters or less.")
        return show_form(request.form)

    db = get_db()
    db.execute(
        """INSERT INTO projects
           (innovator_id, title, summary, description, domain, stage, funding_needed)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (session["user_id"], title, summary, description, domain, stage, int(funding)),
    )
    db.commit()
    flash("Your project is live! ✨")
    return redirect(url_for("dashboard.innovator"))



@projects_bp.route("/<int:project_id>/delete", methods=["POST"])
@login_required
@role_required("innovator")
def delete(project_id):
    db = get_db()
    result = db.execute(
        "DELETE FROM projects WHERE id = ? AND innovator_id = ?",
        (project_id, session["user_id"]),
    )
    db.commit()
    if result.rowcount == 0:
        flash("Project not found.")
    else:
        flash("Project deleted.")
    return redirect(url_for("dashboard.innovator"))    