from flask import session
from flask_socketio import join_room, emit
from extensions import socketio
from db import get_db
from dms.routes import get_thread, can_send, dm_message, valid_reply
from media import delete_media, normalize_gif_url


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
    reply = valid_reply(db, thread_id, data.get("reply_to"))
    gif = normalize_gif_url(body)         # a pasted GIF link becomes a GIF
    cur = db.execute(
        """INSERT INTO dm_messages (thread_id, sender_id, body, reply_to, attachment, attach_type, attach_name)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (thread_id, user_id, "" if gif else body, reply, gif, "image" if gif else None, "gif" if gif else None),
    )
    db.commit()
    emit("message", dm_message(db, cur.lastrowid), to=f"dm-{thread_id}")


@socketio.on("dm_delete")
def on_dm_delete(data):
    """People can delete their own messages (and any attached file) for everyone."""
    message_id = data.get("message_id")
    user_id = session.get("user_id")
    if not user_id or not isinstance(message_id, int):
        return
    db = get_db()
    row = db.execute("SELECT thread_id, attachment FROM dm_messages WHERE id = ? AND sender_id = ?",
                     (message_id, user_id)).fetchone()
    if row is None or get_thread(row["thread_id"], user_id) is None:
        return
    db.execute("DELETE FROM dm_messages WHERE id = ?", (message_id,))
    db.commit()
    delete_media(row["attachment"])
    emit("deleted", {"id": message_id}, to=f"dm-{row['thread_id']}")


@socketio.on("dm_edit")
def on_dm_edit(data):
    """People can edit the text of their own messages."""
    message_id = data.get("message_id")
    user_id = session.get("user_id")
    body = str(data.get("body", "")).strip()
    if not user_id or not isinstance(message_id, int) or not 1 <= len(body) <= 2000:
        return
    db = get_db()
    row = db.execute("SELECT thread_id FROM dm_messages WHERE id = ? AND sender_id = ? AND attachment IS NULL",
                     (message_id, user_id)).fetchone()
    if row is None or get_thread(row["thread_id"], user_id) is None:
        return
    db.execute("UPDATE dm_messages SET body = ?, edited = 1 WHERE id = ?", (body, message_id))
    db.commit()
    emit("edited", {"id": message_id, "body": body}, to=f"dm-{row['thread_id']}")
