from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, session, abort)
from db import get_db
from helpers import login_required
import live
from media import save_many, delete_media, MAX_PER_POST

projects_bp = Blueprint("projects", __name__, url_prefix="/projects")

DOMAINS = ["AI / ML", "HealthTech", "EdTech", "FinTech", "AgriTech",
           "CleanTech", "Social Impact", "Other"]
STAGES = {"idea": "Idea", "prototype": "Prototype", "mvp": "MVP", "growth": "Growth"}
MAX_FUNDING = 10_000_000_000  # ₹1,000 crore


def show_form(form=None, project=None):
    media = []
    if project is not None:
        media = get_db().execute("SELECT * FROM post_media WHERE post_id = ? ORDER BY position, id",
                                 (project["id"],)).fetchall()
    return render_template("projects/new.html", domains=DOMAINS, stages=STAGES,
                           form=form or {}, project=project, media=media,
                           max_media=MAX_PER_POST)


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
    funding = data["funding_needed"]
    if not funding.isdecimal() or not 0 < int(funding) <= MAX_FUNDING:
        return None, "Funding needed must be a whole number between 1 and 1,000 crore."
    if len(data["title"]) > 100:
        return None, "Keep the title to 100 characters or less."
    if len(data["summary"]) > 160:
        return None, "Keep the summary to 160 characters or less."
    data["funding_needed"] = int(data["funding_needed"])
    return data, None


def add_media(db, post_id, saved):
    """Attach freshly saved files to a post, after the ones it already has."""
    start = db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM post_media WHERE post_id = ?",
                       (post_id,)).fetchone()[0]
    db.executemany("INSERT INTO post_media (post_id, path, type, position) VALUES (?, ?, ?, ?)",
                   [(post_id, path, kind, start + i) for i, (path, kind) in enumerate(saved)])


@projects_bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    if request.method == "GET":
        return show_form()

    data, error = validate(request.form)
    saved = []
    if not error:
        try:
            saved = save_many(request.files.getlist("media"), MAX_PER_POST)
        except ValueError as e:
            error = str(e)
    if error:
        flash(error)
        return show_form(request.form)

    data["innovator_id"] = session["user_id"]
    db = get_db()
    cur = db.execute(
        """INSERT INTO projects
           (innovator_id, title, summary, description, domain, stage, funding_needed)
           VALUES (:innovator_id, :title, :summary, :description,
                   :domain, :stage, :funding_needed)""",
        data,
    )
    add_media(db, cur.lastrowid, saved)
    db.commit()
    live.to_feed("post_new", {"id": cur.lastrowid})
    live.touch(["posts"])
    flash("Your project is live! ✨")
    return redirect(url_for("feed.post", post_id=cur.lastrowid))


@projects_bp.route("/<int:project_id>/delete", methods=["POST"])
@login_required
def delete(project_id):
    db = get_db()
    project = db.execute("SELECT id FROM projects WHERE id = ? AND innovator_id = ?",
                         (project_id, session["user_id"])).fetchone()
    if project is None:
        flash("Project not found.")
    else:
        paths = [r["path"] for r in db.execute("SELECT path FROM post_media WHERE post_id = ?",
                                               (project_id,))]
        db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        db.commit()
        for path in paths:
            delete_media(path)
        live.to_feed("post_deleted", {"id": project_id})
        live.touch(["posts"])
        flash("Project deleted.")
    username = db.execute("SELECT username FROM users WHERE id = ?",
                          (session["user_id"],)).fetchone()[0]
    return redirect(url_for("profiles.public", username=username))


@projects_bp.route("/<int:project_id>/edit", methods=["GET", "POST"])
@login_required
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
    saved = []
    removed = request.form.getlist("remove_media", type=int)
    existing = {r["id"]: r["path"] for r in db.execute(
        "SELECT id, path FROM post_media WHERE post_id = ?", (project_id,))}
    if not error:
        removed = [i for i in removed if i in existing]
        room = MAX_PER_POST - (len(existing) - len(removed))
        try:
            saved = save_many(request.files.getlist("media"), max(room, 0))
        except ValueError as e:
            error = str(e)
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
    for media_id in removed:
        db.execute("DELETE FROM post_media WHERE id = ?", (media_id,))
    add_media(db, project_id, saved)
    db.commit()
    for media_id in removed:
        delete_media(existing[media_id])
    from servers import projectlink
    projectlink.on_stage_change(db, project_id, project["stage"], data["stage"], project["funding_needed"], data["funding_needed"])
    live.to_feed("post_changed", {"id": project_id})
    live.touch(["posts"])
    flash("Project updated. ✨")
    return redirect(url_for("feed.post", post_id=project_id))
