from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, session, abort)
from db import get_db
from helpers import login_required, role_required

projects_bp = Blueprint("projects", __name__, url_prefix="/projects")

DOMAINS = ["AI / ML", "HealthTech", "EdTech", "FinTech", "AgriTech",
           "CleanTech", "Social Impact", "Other"]
STAGES = {"idea": "Idea", "prototype": "Prototype", "mvp": "MVP", "growth": "Growth"}


def show_form(form=None, project=None):
    return render_template("projects/new.html", domains=DOMAINS, stages=STAGES,
                           form=form or {}, project=project)


def validate(form):
    """Check a submitted project form. Returns (data, error)."""
    data = {
        "title": form.get("title", "").strip(),
        "summary": form.get("summary", "").strip(),
        "description": form.get("description", "").strip(),
        "domain": form.get("domain", ""),
        "stage": form.get("stage", ""),
        "funding_needed": form.get("funding_needed", "").strip(),
    }
    if not all(data.values()):
        return None, "Please fill in every field."
    if data["domain"] not in DOMAINS or data["stage"] not in STAGES:
        return None, "Please choose a valid domain and stage."
    if not data["funding_needed"].isdigit() or int(data["funding_needed"]) <= 0:
        return None, "Funding needed must be a whole number above 0."
    if len(data["summary"]) > 160:
        return None, "Keep the summary to 160 characters or less."
    data["funding_needed"] = int(data["funding_needed"])
    return data, None


@projects_bp.route("/new", methods=["GET", "POST"])
@login_required
@role_required("innovator")
def new():
    if request.method == "GET":
        return show_form()

    data, error = validate(request.form)
    if error:
        flash(error)
        return show_form(request.form)

    data["innovator_id"] = session["user_id"]
    db = get_db()
    db.execute(
        """INSERT INTO projects
           (innovator_id, title, summary, description, domain, stage, funding_needed)
           VALUES (:innovator_id, :title, :summary, :description,
                   :domain, :stage, :funding_needed)""",
        data,
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


@projects_bp.route("/<int:project_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("innovator")
def edit(project_id):
    db = get_db()
    project = db.execute(
        "SELECT * FROM projects WHERE id = ? AND innovator_id = ?",
        (project_id, session["user_id"]),
    ).fetchone()
    if project is None:
        abort(404)

    if request.method == "GET":
        return show_form(dict(project), project)

    data, error = validate(request.form)
    if error:
        flash(error)
        return show_form(request.form, project)

    data["id"] = project_id
    db.execute(
        """UPDATE projects
           SET title = :title, summary = :summary, description = :description,
               domain = :domain, stage = :stage, funding_needed = :funding_needed
           WHERE id = :id""",
        data,
    )
    db.commit()
    flash("Project updated. ✨")
    return redirect(url_for("dashboard.innovator"))