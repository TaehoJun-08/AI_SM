import re
import secrets
from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, session, abort)
from db import get_db
from helpers import login_required, current_user

spaces_bp = Blueprint("spaces", __name__, url_prefix="/spaces")

ROLES = {"owner": "👑 Owner", "member": "🛠 Member", "sponsor": "💎 Sponsor"}


def get_role(project_id, user_id):
    """The user's role in a project space, or None if they're not in it."""
    db = get_db()
    if db.execute("SELECT 1 FROM projects WHERE id = ? AND innovator_id = ?",
                  (project_id, user_id)).fetchone():
        return "owner"
    if db.execute("SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ?",
                  (project_id, user_id)).fetchone():
        return "member"
    if db.execute("""SELECT 1 FROM interests WHERE project_id = ?
                     AND sponsor_id = ? AND status = 'accepted'""",
                  (project_id, user_id)).fetchone():
        return "sponsor"
    return None


def set_up_space(project_id):
    """Give a project its invite code and default channels (safe to call often)."""
    db = get_db()
    db.execute("INSERT OR IGNORE INTO project_invites (project_id, code) VALUES (?, ?)",
               (project_id, secrets.token_hex(4).upper()))
    db.execute("INSERT OR IGNORE INTO channels (project_id, name) VALUES (?, 'general')",
               (project_id,))
    db.execute("""INSERT OR IGNORE INTO channels (project_id, name, team_only)
                  VALUES (?, 'team', 1)""", (project_id,))
    db.commit()


@spaces_bp.route("/")
@login_required
def index():
    uid = session["user_id"]
    spaces = get_db().execute(
        """SELECT id, title, domain FROM projects WHERE innovator_id = ?
           UNION
           SELECT p.id, p.title, p.domain FROM projects p
             JOIN project_members m ON m.project_id = p.id WHERE m.user_id = ?
           UNION
           SELECT p.id, p.title, p.domain FROM projects p
             JOIN interests i ON i.project_id = p.id
             WHERE i.sponsor_id = ? AND i.status = 'accepted'
           ORDER BY title""",
        (uid, uid, uid),
    ).fetchall()
    return render_template("spaces/index.html", spaces=spaces)


@spaces_bp.route("/join", methods=["POST"])
@login_required
def join():
    if current_user()["role"] != "innovator":
        flash("Only innovators can join a team with a code.")
        return redirect(url_for("spaces.index"))
    code = request.form.get("code", "").strip().upper()
    invite = get_db().execute(
        "SELECT project_id FROM project_invites WHERE code = ?", (code,)
    ).fetchone()
    if invite is None:
        flash("That invite code doesn't exist.")
        return redirect(url_for("spaces.index"))
    project_id = invite["project_id"]
    if get_role(project_id, session["user_id"]) is None:
        db = get_db()
        db.execute("INSERT INTO project_members (project_id, user_id) VALUES (?, ?)",
                   (project_id, session["user_id"]))
        db.commit()
        flash("Welcome to the team! 🛠")
    return redirect(url_for("spaces.view", project_id=project_id))


@spaces_bp.route("/<int:project_id>")
@spaces_bp.route("/<int:project_id>/<int:channel_id>")
@login_required
def view(project_id, channel_id=None):
    role = get_role(project_id, session["user_id"])
    if role is None:
        abort(404)
    set_up_space(project_id)
    db = get_db()
    project = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    channels = db.execute(
        "SELECT * FROM channels WHERE project_id = ? AND (team_only = 0 OR ?) ORDER BY id",
        (project_id, role != "sponsor"),
    ).fetchall()
    current = next((c for c in channels if c["id"] == channel_id), channels[0])
    members = db.execute(
        """SELECT u.id, u.full_name, 'owner' AS role FROM projects p
             JOIN users u ON u.id = p.innovator_id WHERE p.id = :pid
           UNION ALL
           SELECT u.id, u.full_name, 'member' FROM project_members m
             JOIN users u ON u.id = m.user_id WHERE m.project_id = :pid
           UNION ALL
           SELECT u.id, u.full_name, 'sponsor' FROM interests i
             JOIN users u ON u.id = i.sponsor_id
             WHERE i.project_id = :pid AND i.status = 'accepted'""",
        {"pid": project_id},
    ).fetchall()
    invite = db.execute("SELECT code FROM project_invites WHERE project_id = ?",
                        (project_id,)).fetchone()
    return render_template("spaces/view.html", project=project, role=role,
                           channels=channels, current=current, members=members,
                           code=invite["code"] if role == "owner" else None, roles=ROLES)  


@spaces_bp.route("/<int:project_id>/channels", methods=["POST"])
@login_required
def new_channel(project_id):
    if get_role(project_id, session["user_id"]) != "owner":
        abort(404)
    name = request.form.get("name", "").strip().lower().replace(" ", "-")
    if not re.fullmatch(r"[a-z0-9-]{1,30}", name):
        flash("Channel names can use letters, numbers and dashes (max 30).")
        return redirect(url_for("spaces.view", project_id=project_id))
    db = get_db()
    result = db.execute(
        "INSERT OR IGNORE INTO channels (project_id, name, team_only) VALUES (?, ?, ?)",
        (project_id, name, 1 if request.form.get("team_only") else 0),
    )
    db.commit()
    if result.rowcount == 0:
        flash(f"#{name} already exists.")
        return redirect(url_for("spaces.view", project_id=project_id))
    return redirect(url_for("spaces.view", project_id=project_id,
                            channel_id=result.lastrowid))