from flask import session
from flask_socketio import join_room, emit
from extensions import socketio
from db import get_db
from spaces.routes import get_role


def allowed_channel(channel_id):
    """The channel row if the logged-in user may use it, otherwise None."""
    user_id = session.get("user_id")
    if user_id is None or not isinstance(channel_id, int):
        return None
    channel = get_db().execute(
        "SELECT * FROM channels WHERE id = ?", (channel_id,)
    ).fetchone()
    if channel is None:
        return None
    role = get_role(channel["project_id"], user_id)
    if role is None or (channel["team_only"] and role == "sponsor"):
        return None
    return channel


@socketio.on("join")
def on_join(data):
    channel_id = data.get("channel_id")
    if allowed_channel(channel_id):
        join_room(f"channel-{channel_id}")


@socketio.on("send")
def on_send(data):
    channel_id = data.get("channel_id")
    body = str(data.get("body", "")).strip()
    if not 1 <= len(body) <= 2000 or not allowed_channel(channel_id):
        return
    db = get_db()
    cur = db.execute(
        "INSERT INTO messages (channel_id, sender_id, body) VALUES (?, ?, ?)",
        (channel_id, session["user_id"], body),
    )
    db.commit()
    message = db.execute(
        """SELECT m.id, m.body, m.created_at, m.sender_id, u.full_name
           FROM messages m JOIN users u ON u.id = m.sender_id
           WHERE m.id = ?""",
        (cur.lastrowid,),
    ).fetchone()
    emit("message", dict(message), to=f"channel-{channel_id}")