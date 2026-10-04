from flask import Blueprint, redirect, url_for, flash, session, abort
from db import get_db
from helpers import login_required, role_required

interests_bp = Blueprint("interests", __name__, url_prefix="/interests")


@interests_bp.route("/project/<int:project_id>", methods=["POST"])
@login_required
@role_required("sponsor")
def express(project_id):
    db = get_db()
    project = db.execute(
        "SELECT id FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if project is None:
        abort(404)
    result = db.execute(
        "INSERT OR IGNORE INTO interests (sponsor_id, project_id) VALUES (?, ?)",
        (session["user_id"], project_id),
    )
    db.commit()
    if result.rowcount:
        flash("Interest sent! 💌 The innovator will see it on their dashboard.")
    else:
        flash("You've already shown interest in this project.")
    return redirect(url_for("dashboard.sponsor"))


@interests_bp.route("/<int:interest_id>/<action>", methods=["POST"])
@login_required
@role_required("innovator")
def respond(interest_id, action):
    if action not in ("accept", "decline"):
        abort(404)
    db = get_db()
    interest = db.execute(
        """SELECT i.id FROM interests i
           JOIN projects p ON p.id = i.project_id
           WHERE i.id = ? AND p.innovator_id = ? AND i.status = 'pending'""",
        (interest_id, session["user_id"]),
    ).fetchone()
    if interest is None:
        abort(404)
    status = "accepted" if action == "accept" else "declined"
    db.execute("UPDATE interests SET status = ? WHERE id = ?", (status, interest_id))
    db.commit()
    if status == "accepted":
        flash("You're connected! 🎉 You can now see each other's email.")
    else:
        flash("Interest declined.")
    return redirect(url_for("dashboard.innovator"))    