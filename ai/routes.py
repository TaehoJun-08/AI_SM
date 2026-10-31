"""The dedicated Nova tab: a private, persistent, streaming chat with memory, edits and (approved) actions."""
import json
import mimetypes
import os

from flask import Blueprint, Response, abort, jsonify, render_template, request, session, stream_with_context

import live
from db import get_db, open_connection
from helpers import login_required
from media import save_attachment
from ai import gemini, memory

ai_bp = Blueprint("ai", __name__, url_prefix="/nova")

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def _history(db, user_id, before_id=None):
    if before_id is None:
        rows = db.execute("SELECT role, body FROM ai_messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                          (user_id, gemini.HISTORY_TURNS)).fetchall()
        return [dict(r) for r in reversed(rows)]
    rows = db.execute(
        """SELECT role, body FROM ai_messages WHERE user_id = ? AND id < ?
           ORDER BY id DESC LIMIT ?""", (user_id, before_id, gemini.HISTORY_TURNS)).fetchall()
    return [dict(r) for r in reversed(rows)]


def _pending_actions(db, user_id):
    rows = db.execute("SELECT * FROM ai_actions WHERE user_id = ? AND status = 'pending' ORDER BY id",
                      (user_id,)).fetchall()
    return [dict(r, payload=json.loads(r["payload"])) for r in rows]


def _read_image(attachment, attach_type):
    """(bytes, mime) for an attachment Nova can actually see, or None (not an image, or a pasted GIF link)."""
    if not attachment or attach_type != "image" or attachment.startswith("http"):
        return None
    path = os.path.join(STATIC_DIR, attachment)
    try:
        with open(path, "rb") as f:
            return f.read(), (mimetypes.guess_type(path)[0] or "image/jpeg")
    except OSError:
        return None


def _stream_response(me, history, user_text, image, start_meta=None):
    """Runs the whole reply in its own, independent DB connection -- not Flask's per-request `g.db` --
    because this generator keeps running (and needs the database) after the view function has returned,
    which is exactly when Flask tears the request-scoped connection down."""
    def generate():
        conn = open_connection()
        try:
            if start_meta:
                yield json.dumps({"type": "start", **start_meta}) + "\n"
            full, completed = "", False
            try:
                for event in gemini.stream_reply(conn, me, history, user_text, memory.facts(conn, me), image):
                    if event["type"] == "token":
                        full += event["text"]
                    yield json.dumps(event) + "\n"
                completed = True
            finally:
                if full.strip():
                    body = full if completed else full + "\n\n_(stopped)_"
                    conn.execute("INSERT INTO ai_messages (user_id, role, body) VALUES (?, 'model', ?)", (me, body))
                    conn.commit()
            if completed:
                yield json.dumps({"type": "done", "actions": _pending_actions(conn, me)}) + "\n"
        finally:
            conn.close()
    return Response(stream_with_context(generate()), mimetype="application/x-ndjson")


@ai_bp.route("/")
@login_required
def thread():
    db = get_db()
    me = session["user_id"]
    messages = db.execute("SELECT * FROM ai_messages WHERE user_id = ? ORDER BY id", (me,)).fetchall()
    facts = db.execute("SELECT * FROM ai_memory WHERE user_id = ? ORDER BY id", (me,)).fetchall()
    return render_template("ai/thread.html", messages=messages, actions=_pending_actions(db, me),
                           facts=facts, connected=gemini.available())


@ai_bp.route("/send", methods=["POST"])
@login_required
def send():
    db = get_db()
    me = session["user_id"]
    text = (request.form.get("text") or "").strip()[:4000]
    attachment = attach_type = attach_name = None
    file = request.files.get("file")
    if file and file.filename:
        try:
            attachment, attach_type, attach_name = save_attachment(file)
        except ValueError as e:
            return jsonify(error=str(e)), 400
    if not text and not attachment:
        return jsonify(error="Say something first."), 400
    history = _history(db, me)
    cur = db.execute(
        """INSERT INTO ai_messages (user_id, role, body, attachment, attach_type, attach_name)
           VALUES (?, 'user', ?, ?, ?, ?)""", (me, text, attachment, attach_type, attach_name))
    db.commit()
    image = _read_image(attachment, attach_type)
    return _stream_response(me, history, text or "(see the attached image)", image,
                            start_meta={"message_id": cur.lastrowid, "attachment": attachment,
                                        "attach_type": attach_type, "attach_name": attach_name})


@ai_bp.route("/messages/<int:message_id>/edit", methods=["POST"])
@login_required
def edit(message_id):
    db = get_db()
    me = session["user_id"]
    msg = db.execute("SELECT * FROM ai_messages WHERE id = ? AND user_id = ? AND role = 'user'",
                     (message_id, me)).fetchone()
    if msg is None:
        abort(404)
    text = (request.form.get("text") or "").strip()[:4000]
    if not text:
        return jsonify(error="Say something first."), 400
    history = _history(db, me, before_id=message_id)
    db.execute("DELETE FROM ai_messages WHERE user_id = ? AND id >= ?", (me, message_id))
    cur = db.execute(
        """INSERT INTO ai_messages (user_id, role, body, attachment, attach_type, attach_name)
           VALUES (?, 'user', ?, ?, ?, ?)""", (me, text, msg["attachment"], msg["attach_type"], msg["attach_name"]))
    db.commit()
    image = _read_image(msg["attachment"], msg["attach_type"])
    return _stream_response(me, history, text, image, start_meta={"message_id": cur.lastrowid, "replaced": message_id})


@ai_bp.route("/memory/<int:fact_id>/forget", methods=["POST"])
@login_required
def forget(fact_id):
    memory.forget(get_db(), session["user_id"], fact_id)
    return jsonify(ok=True)


@ai_bp.route("/actions/<int:action_id>/<decision>", methods=["POST"])
@login_required
def decide(action_id, decision):
    if decision not in ("approve", "reject"):
        abort(404)
    db = get_db()
    me = session["user_id"]
    action = db.execute("SELECT * FROM ai_actions WHERE id = ? AND user_id = ? AND status = 'pending'",
                        (action_id, me)).fetchone()
    if action is None:
        abort(404)
    payload = json.loads(action["payload"])
    note = _execute(db, me, action["kind"], payload) if decision == "approve" else "Discarded."
    db.execute("UPDATE ai_actions SET status = ? WHERE id = ?",
              ("approved" if decision == "approve" else "rejected", action_id))
    db.commit()
    return jsonify(ok=True, note=note)


def _execute(db, me, kind, payload):
    if kind == "send_dm":
        return _send_dm(db, me, payload)
    if kind == "post_update":
        return _post_update(db, me, payload)
    return "Unknown action."


def _send_dm(db, me, payload):
    to = payload["to"]
    a, b = min(me, to), max(me, to)
    db.execute("""INSERT INTO dm_threads (user_a, user_b, requested_by) VALUES (?, ?, ?)
                  ON CONFLICT (user_a, user_b) DO NOTHING""", (a, b, me))
    thread = db.execute("SELECT id, status, requested_by FROM dm_threads WHERE user_a = ? AND user_b = ?",
                        (a, b)).fetchone()
    if thread["status"] == "declined" and thread["requested_by"] != me:
        return "They aren't accepting messages from you right now, so this wasn't sent."
    cur = db.execute("INSERT INTO dm_messages (thread_id, sender_id, body) VALUES (?, ?, ?)",
                     (thread["id"], me, payload["message"][:2000]))
    db.commit()
    from dms.routes import dm_message
    from extensions import socketio
    socketio.emit("message", dm_message(db, cur.lastrowid), to=f"dm-{thread['id']}")
    live.inbox_changed(thread["id"])
    live.notify_dm(db, thread["id"], me, payload["message"])
    return f"Sent to @{payload['username']}."


def _post_update(db, me, payload):
    project_id = payload["project_id"]
    owner = db.execute("SELECT innovator_id, title FROM projects WHERE id = ?", (project_id,)).fetchone()
    if owner is None or owner["innovator_id"] != me:
        return "That project isn't yours anymore, so this wasn't posted."
    body = payload["body"][:500]
    db.execute("INSERT INTO project_updates (project_id, author_id, body) VALUES (?, ?, ?)", (project_id, me, body))
    db.commit()
    from servers.projectlink import notify_followers, project_post
    project_post(db, project_id, embed={"title": "📣 Project update", "description": body, "color": "#f59e0b"})
    notify_followers(db, project_id, f"📣 {owner['title']}", body)
    live.touch(["posts"])
    return "Posted to your project."
