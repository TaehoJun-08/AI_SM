from flask import session
from flask_socketio import join_room, emit
from extensions import socketio
from db import get_db
from servers.routes import channel_for, can_write, MSG_SELECT, valid_reply
from media import delete_media, normalize_gif_url
import live
import unread


@socketio.on("srv_join")
def on_join(data):
    channel_id = data.get("channel_id")
    user_id = session.get("user_id")
    if user_id and isinstance(channel_id, int) and channel_for(channel_id, user_id)[1]:
        join_room(f"srv-{channel_id}")


@socketio.on("srv_send")
def on_send(data):
    channel_id = data.get("channel_id")
    user_id = session.get("user_id")
    body = str(data.get("body", "")).strip()
    if not user_id or not isinstance(channel_id, int) or not 1 <= len(body) <= 2000:
        return
    m, channel = channel_for(channel_id, user_id)
    if channel is None or not can_write(m, channel):
        return
    db = get_db()
    reply = valid_reply(db, channel_id, data.get("reply_to"))
    gif = normalize_gif_url(body)         # a pasted GIF link becomes a GIF
    cur = db.execute(
        """INSERT INTO server_messages (channel_id, sender_id, body, reply_to, attachment, attach_type, attach_name)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (channel_id, user_id, "" if gif else body, reply, gif, "image" if gif else None, "gif" if gif else None))
    db.commit()
    message = db.execute(f"{MSG_SELECT} WHERE m.id = :mid",
                         {"mid": cur.lastrowid, "sid": channel["server_id"]}).fetchone()
    emit("message", dict(message), to=f"srv-{channel_id}")
    live.notify_channel(db, channel, user_id, live.preview("" if gif else body, "image" if gif else None))


@socketio.on("srv_delete")
def on_delete(data):
    """Delete your own message; owners and admins can delete anyone's."""
    message_id = data.get("message_id")
    user_id = session.get("user_id")
    if not user_id or not isinstance(message_id, int):
        return
    db = get_db()
    row = db.execute("SELECT channel_id, sender_id, attachment FROM server_messages WHERE id = ?",
                     (message_id,)).fetchone()
    if row is None:
        return
    m, channel = channel_for(row["channel_id"], user_id)
    if channel is None or not (row["sender_id"] == user_id or m["is_manager"]):
        return
    db.execute("DELETE FROM server_messages WHERE id = ?", (message_id,))
    db.commit()
    delete_media(row["attachment"])
    emit("deleted", {"id": message_id}, to=f"srv-{row['channel_id']}")


@socketio.on("srv_edit")
def on_edit(data):
    """People can edit the text of their own messages."""
    message_id = data.get("message_id")
    user_id = session.get("user_id")
    body = str(data.get("body", "")).strip()
    if not user_id or not isinstance(message_id, int) or not 1 <= len(body) <= 2000:
        return
    db = get_db()
    row = db.execute("SELECT channel_id FROM server_messages WHERE id = ? AND sender_id = ? AND attachment IS NULL",
                     (message_id, user_id)).fetchone()
    if row is None:
        return
    m, channel = channel_for(row["channel_id"], user_id)
    if channel is None or not can_write(m, channel):
        return
    db.execute("UPDATE server_messages SET body = ?, edited = 1 WHERE id = ?", (body, message_id))
    db.commit()
    emit("edited", {"id": message_id, "body": body}, to=f"srv-{row['channel_id']}")


@socketio.on("srv_seen")
def on_srv_seen(data):
    """The channel is open on someone's screen and a message arrived: it counts as read."""
    channel_id = data.get("channel_id")
    user_id = session.get("user_id")
    if user_id and isinstance(channel_id, int) and channel_for(channel_id, user_id)[1]:
        unread.mark_channel(channel_id, user_id)
        live.refresh_badges(user_id)
