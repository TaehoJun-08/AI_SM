from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, session, abort, jsonify)
from db import get_db
from helpers import login_required
from extensions import socketio
from chatmedia import collect
import live
import unread

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
        """SELECT t.*, u.full_name AS other_name, u.username AS other_username, u.avatar AS other_avatar, u.id AS other_id,
                  (SELECT COALESCE(NULLIF(body, ''), CASE attach_type
                              WHEN 'image' THEN '📷 Photo' WHEN 'video' THEN '🎥 Video'
                              WHEN 'audio' THEN '🎵 Audio' ELSE '📎 File' END)
                   FROM dm_messages WHERE thread_id = t.id ORDER BY id DESC LIMIT 1) AS last_body,
                  (SELECT COUNT(*) FROM dm_messages m WHERE m.thread_id = t.id AND m.sender_id != :me
                     AND m.id > COALESCE((SELECT last_id FROM dm_reads WHERE thread_id = t.id AND user_id = :me), 0)
                     AND NOT EXISTS (SELECT 1 FROM mutes WHERE user_id = :me AND kind = 'dm' AND target_id = t.id)) AS unread
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


DM_MSG = """
    SELECT m.id, m.body, m.created_at, m.sender_id, m.edited, m.reply_to, u.full_name, u.avatar,
           m.attachment, m.attach_type, m.attach_name, ru.username AS reply_name,
           COALESCE(NULLIF(rm.body, ''), CASE rm.attach_type WHEN 'image' THEN '📷 Photo' WHEN 'video' THEN '🎥 Video' WHEN 'audio' THEN '🎵 Audio' ELSE '📎 File' END) AS reply_text
    FROM dm_messages m JOIN users u ON u.id = m.sender_id
    LEFT JOIN dm_messages rm ON rm.id = m.reply_to LEFT JOIN users ru ON ru.id = rm.sender_id
"""


def dm_message(db, message_id):
    """One message as a dict, in the shape the chat page expects."""
    return dict(db.execute(f"{DM_MSG} WHERE m.id = ?", (message_id,)).fetchone())


def valid_reply(db, thread_id, reply_id):
    """The id to store as reply_to: only a message from this same chat counts."""
    if not isinstance(reply_id, int):
        return None
    row = db.execute("SELECT id FROM dm_messages WHERE id = ? AND thread_id = ?", (reply_id, thread_id)).fetchone()
    return row["id"] if row else None


def sidebar(me):
    """Everything the shared inbox sidebar needs."""
    chats, requests = load_threads(me)
    q = request.args.get("q", "").strip()
    people = []
    if q:
        term = q.lstrip("@")
        people = get_db().execute(
            """SELECT id, full_name, username, avatar FROM users
               WHERE (username LIKE :like OR full_name LIKE :like) AND id != :me
               ORDER BY username = :term DESC, username LIMIT 10""",
            {"like": f"%{term}%", "term": term, "me": me},
        ).fetchall()
    return {"chats": chats, "requests": requests, "people": people, "q": q}


@dms_bp.route("/")
@login_required
def inbox():
    return render_template("dms/inbox.html", **sidebar(session["user_id"]))


@dms_bp.route("/list")
@login_required
def chat_list():
    """Just the chat list, so an open page can refresh it the moment something happens."""
    chats, requests = load_threads(session["user_id"])
    active = request.args.get("active", type=int)
    return render_template("dms/_list.html", chats=chats, requests=requests, people=[], q="",
                           thread={"id": active} if active else None)


@dms_bp.route("/start/<int:user_id>", methods=["POST"])
@login_required
def start(user_id):
    me = session["user_id"]
    db = get_db()
    if user_id == me or not db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
        abort(404)
    a, b = min(me, user_id), max(me, user_id)
    existing = db.execute("SELECT 1 FROM dm_threads WHERE user_a = ? AND user_b = ?", (a, b)).fetchone()
    target = db.execute("SELECT dm_policy, username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not existing and target["dm_policy"] == "none":
        flash(f"@{target['username']} isn't accepting new messages right now.")
        return redirect(request.referrer or url_for("dms.inbox"))
    db.execute("INSERT OR IGNORE INTO dm_threads (user_a, user_b, requested_by) VALUES (?, ?, ?)",
               (a, b, me))
    db.execute("""UPDATE dm_threads SET status = 'pending', requested_by = ?
                  WHERE user_a = ? AND user_b = ? AND status = 'declined'
                    AND requested_by != ?""", (me, a, b, me))
    db.commit()
    thread = db.execute("SELECT id FROM dm_threads WHERE user_a = ? AND user_b = ?",
                        (a, b)).fetchone()
    live.inbox_changed(thread["id"])
    live.touch(["dm"], users=[a, b])
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
    seen = db.execute("SELECT last_id FROM dm_reads WHERE thread_id = ? AND user_id = ?",
                      (thread_id, other_id)).fetchone()
    top = unread.mark_dm(thread_id, me)
    socketio.emit("seen", {"user_id": me, "upto": top}, to=f"dm-{thread_id}")
    other = db.execute("SELECT id, full_name, username, avatar FROM users WHERE id = ?",
                       (other_id,)).fetchone()
    messages = db.execute(
        f"SELECT * FROM ({DM_MSG} WHERE m.thread_id = ? ORDER BY m.id DESC LIMIT 50) ORDER BY id",
        (thread_id,),
    ).fetchall()
    return render_template("dms/thread.html", thread=t, other=other, messages=messages,
                           can_send=can_send(t, me), seen_upto=seen["last_id"] if seen else 0, **sidebar(me))


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
    live.inbox_changed(thread_id, reload=True)
    live.touch(["dm"], users=[t["user_a"], t["user_b"]])
    if action == "accept":
        live.notify([t["requested_by"]], "dm", "Request accepted",
                    f"@{live.username(db, me)} accepted your message request",
                    f"/messages/{thread_id}", key=f"dm:{thread_id}")
    if action == "accept":
        return redirect(url_for("dms.thread", thread_id=thread_id))
    flash("Request declined.")
    return redirect(url_for("dms.inbox"))

@dms_bp.route("/<int:thread_id>/attach", methods=["POST"])
@login_required
def attach(thread_id):
    """Send photos, videos, audio, files or a GIF link into this chat."""
    me = session["user_id"]
    t = get_thread(thread_id, me)
    if t is None or not can_send(t, me):
        abort(403)
    try:
        items = collect(request)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    db = get_db()
    reply = valid_reply(db, thread_id, request.form.get("reply_to", type=int))
    for path, kind, name in items:
        cur = db.execute(
            """INSERT INTO dm_messages (thread_id, sender_id, body, attachment, attach_type, attach_name, reply_to)
               VALUES (?, ?, '', ?, ?, ?, ?)""", (thread_id, me, path, kind, name, reply))
        reply = None                      # only the first file of a batch quotes the message
        db.commit()
        socketio.emit("message", dm_message(db, cur.lastrowid), to=f"dm-{thread_id}")
        live.notify_dm(db, thread_id, me, live.preview("", kind))
    live.inbox_changed(thread_id)
    return jsonify(sent=len(items))
