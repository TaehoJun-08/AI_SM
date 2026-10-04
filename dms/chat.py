from flask import session
from flask_socketio import join_room, emit
from extensions import socketio
from db import get_db
from dms.routes import get_thread, can_send


@socketio.on("dm_join")
def on_dm_join(data):
    thread_id = data.get("thread_id")
    user_id = session.get("user_id")
    if user_id and isinstance(thread_id, int) and get_thread(thread_id, user_id):
        join_room(f"dm-{thread_id}")


@socketio.on("dm_send")
def on_dm_send(data):
    thread_id = data.get("thread_id")
    user_id = session.get("user_id")
    body = str(data.get("body", "")).strip()
    if not user_id or not isinstance(thread_id, int) or not 1 <= len(body) <= 2000:
        return
    thread = get_thread(thread_id, user_id)
    if thread is None or not can_send(thread, user_id):
        return
    db = get_db()
    cur = db.execute(
        "INSERT INTO dm_messages (thread_id, sender_id, body) VALUES (?, ?, ?)",
        (thread_id, user_id, body),
    )
    db.commit()
    message = db.execute(
        """SELECT m.id, m.body, m.created_at, m.sender_id, u.full_name
           FROM dm_messages m JOIN users u ON u.id = m.sender_id
           WHERE m.id = ?""",
        (cur.lastrowid,),
    ).fetchone()
    emit("message", dict(message), to=f"dm-{thread_id}")