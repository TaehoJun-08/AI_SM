from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, session, abort)
from db import get_db
from helpers import login_required

dms_bp = Blueprint("dms", __name__, url_prefix="/messages")


def get_thread(thread_id, user_id):
    """The DM thread if the user is one of its two people, otherwise None."""
    return get_db().execute(
        "SELECT * FROM dm_threads WHERE id = ? AND ? IN (user_a, user_b)",
        (thread_id, user_id),
    ).fetchone()


def can_send(thread, user_id):
    """Accepted: both can write. Pending: only the requester. Declined: nobody."""
    if thread["status"] == "accepted":
        return True
    return thread["status"] == "pending" and thread["requested_by"] == user_id


def load_threads(me):
    """Split my conversations into (chats, requests waiting for my answer)."""
    rows = get_db().execute(
        """SELECT t.*, u.full_name AS other_name, u.username AS other_username,
                  (SELECT body FROM dm_messages WHERE thread_id = t.id
                   ORDER BY id DESC LIMIT 1) AS last_body
           FROM dm_threads t
           JOIN users u ON u.id = CASE WHEN t.user_a = :me THEN t.user_b ELSE t.user_a END
           WHERE :me IN (t.user_a, t.user_b)
             AND NOT (t.status = 'declined' AND t.requested_by != :me)
           ORDER BY (SELECT MAX(id) FROM dm_messages WHERE thread_id = t.id) DESC,
                    t.id DESC""",
        {"me": me},
    ).fetchall()
    requests = [t for t in rows if t["status"] == "pending" and t["requested_by"] != me]
    chats = [t for t in rows if t not in requests]
    return chats, requests


def sidebar(me):
    """Everything the shared inbox sidebar needs."""
    chats, requests = load_threads(me)
    q = request.args.get("q", "").strip()
    people = []
    if q:
        term = q.lstrip("@")
        people = get_db().execute(
            """SELECT id, full_name, username, role FROM users
               WHERE (username LIKE :like OR full_name LIKE :like) AND id != :me
               ORDER BY username = :term DESC, username LIMIT 10""",
            {"like": f"%{term}%", "term": term, "me": me},
        ).fetchall()
    return {"chats": chats, "requests": requests, "people": people, "q": q}


@dms_bp.route("/")
@login_required
def inbox():
    return render_template("dms/inbox.html", **sidebar(session["user_id"]))


@dms_bp.route("/start/<int:user_id>", methods=["POST"])
@login_required
def start(user_id):
    me = session["user_id"]
    db = get_db()
    if user_id == me or not db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
        abort(404)
    a, b = min(me, user_id), max(me, user_id)
    db.execute("INSERT OR IGNORE INTO dm_threads (user_a, user_b, requested_by) VALUES (?, ?, ?)",
               (a, b, me))
    db.execute("""UPDATE dm_threads SET status = 'pending', requested_by = ?
                  WHERE user_a = ? AND user_b = ? AND status = 'declined'
                    AND requested_by != ?""", (me, a, b, me))
    db.commit()
    thread = db.execute("SELECT id FROM dm_threads WHERE user_a = ? AND user_b = ?",
                        (a, b)).fetchone()
    return redirect(url_for("dms.thread", thread_id=thread["id"]))


@dms_bp.route("/<int:thread_id>")
@login_required
def thread(thread_id):
    me = session["user_id"]
    t = get_thread(thread_id, me)
    if t is None or (t["status"] == "declined" and t["requested_by"] != me):
        abort(404)
    db = get_db()
    other_id = t["user_b"] if t["user_a"] == me else t["user_a"]
    other = db.execute("SELECT id, full_name, username, role FROM users WHERE id = ?",
                       (other_id,)).fetchone()
    messages = db.execute(
        """SELECT * FROM (
               SELECT m.id, m.body, m.created_at, m.sender_id, u.full_name
               FROM dm_messages m JOIN users u ON u.id = m.sender_id
               WHERE m.thread_id = ? ORDER BY m.id DESC LIMIT 50
           ) ORDER BY id""",
        (thread_id,),
    ).fetchall()
    return render_template("dms/thread.html", thread=t, other=other, messages=messages,
                           can_send=can_send(t, me), **sidebar(me))


@dms_bp.route("/<int:thread_id>/<action>", methods=["POST"])
@login_required
def respond(thread_id, action):
    me = session["user_id"]
    t = get_thread(thread_id, me)
    if (action not in ("accept", "decline") or t is None
            or t["status"] != "pending" or t["requested_by"] == me):
        abort(404)
    db = get_db()
    db.execute("UPDATE dm_threads SET status = ? WHERE id = ?",
               ("accepted" if action == "accept" else "declined", thread_id))
    db.commit()
    if action == "accept":
        return redirect(url_for("dms.thread", thread_id=thread_id))
    flash("Request declined.")
    return redirect(url_for("dms.inbox"))