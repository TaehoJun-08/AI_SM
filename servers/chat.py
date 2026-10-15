import json
import time
from flask import session
from flask_socketio import join_room, emit
from extensions import socketio
from db import get_db
from servers.routes import channel_for, can_write
from servers.messages import (valid_reply, post_message, one, serialize, tell_only, MSG_SELECT)
from servers import companions
from media import delete_media, normalize_gif_url
import live
import unread

_last_sent = {}       # (user id, channel id) -> time of the last message, for slow-mode


def log_action(db, server_id, user_id, action, detail=""):
    db.execute("INSERT INTO audit_log (server_id, actor_id, action, detail) VALUES (?, ?, ?, ?)",
               (server_id, user_id, action, detail[:300]))


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
    if channel is None:
        return
    if m["timeout_until"]:
        return tell_only(user_id, channel_id, f"⏳ You are timed out until {m['timeout_until'][11:16]}.")
    if not can_write(m, channel):
        return
    db = get_db()
    server = m["server"]
    if body.startswith("/"):
        result = companions.run_command(db, server, m, channel, user_id, body)
        if result and result[0] == "done":
            return
        if result and result[0] == "rewrite":
            body = result[1].strip()
            if not body:
                return
    gap = channel["slowmode"] if "manage_messages" not in m["perms"] else 0
    if gap and time.time() - _last_sent.get((user_id, channel_id), 0) < gap:
        return tell_only(user_id, channel_id, f"🐢 Slow mode: wait {gap} seconds between messages.")
    reason = companions.screen(db, server["id"], m, user_id, body, channel)
    if reason:
        log_action(db, server["id"], user_id, "guard.block", reason)
        db.commit()
        return tell_only(user_id, channel_id, f"🛡️ Guard didn't send your message: {reason}.")
    _last_sent[(user_id, channel_id)] = time.time()
    reply = valid_reply(db, channel_id, data.get("reply_to"))
    gif = normalize_gif_url(body)         # a pasted GIF link becomes a GIF
    post_message(db, channel, user_id, "" if gif else body, reply_to=reply, attachment=gif,
                 attach_type="image" if gif else None, attach_name="gif" if gif else None,
                 can_ping_all="mention_all" in m["perms"])
    companions.on_message(db, server, channel, user_id)


@socketio.on("srv_delete")
def on_delete(data):
    """Delete your own message; people with Manage messages can delete anyone's."""
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
    if channel is None or not (row["sender_id"] == user_id or "manage_messages" in m["perms"]):
        return
    if row["sender_id"] != user_id:
        log_action(db, channel["server_id"], user_id, "message.delete", f"in #{channel['name']}")
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
    row = db.execute("SELECT channel_id FROM server_messages WHERE id = ? AND sender_id = ? AND attachment IS NULL AND embed IS NULL",
                     (message_id, user_id)).fetchone()
    if row is None:
        return
    m, channel = channel_for(row["channel_id"], user_id)
    if channel is None or not can_write(m, channel):
        return
    reason = companions.screen(db, channel["server_id"], m, user_id, body)
    if reason:
        return tell_only(user_id, channel["id"], f"🛡️ Guard didn't save your edit: {reason}.")
    db.execute("UPDATE server_messages SET body = ?, edited = 1 WHERE id = ?", (body, message_id))
    db.commit()
    emit("edited", {"id": message_id, "body": body}, to=f"srv-{row['channel_id']}")


def reading(data, user_id):
    """(membership, channel, message row) for an action on a message the person may see, else Nones."""
    message_id = data.get("message_id")
    if not user_id or not isinstance(message_id, int):
        return None, None, None
    row = get_db().execute("SELECT * FROM server_messages WHERE id = ?", (message_id,)).fetchone()
    if row is None:
        return None, None, None
    m, channel = channel_for(row["channel_id"], user_id)
    return (m, channel, row) if channel else (None, None, None)


@socketio.on("srv_react")
def on_react(data):
    user_id = session.get("user_id")
    m, channel, row = reading(data, user_id)
    emoji = str(data.get("emoji", "")).strip()
    if row is None or "react" not in m["perms"] or m["timeout_until"] or not 1 <= len(emoji) <= 40 or " " in emoji:
        return
    db = get_db()
    if emoji.startswith(":") and not db.execute("SELECT 1 FROM server_emojis WHERE server_id = ? AND name = ?",
                                                (channel["server_id"], emoji.strip(":"))).fetchone():
        return
    have = db.execute("SELECT 1 FROM message_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
                      (row["id"], user_id, emoji)).fetchone()
    if have:
        db.execute("DELETE FROM message_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
                   (row["id"], user_id, emoji))
    else:
        kinds = db.execute("SELECT COUNT(DISTINCT emoji) FROM message_reactions WHERE message_id = ?", (row["id"],)).fetchone()[0]
        if kinds >= 20:
            return
        db.execute("INSERT INTO message_reactions (message_id, user_id, emoji) VALUES (?, ?, ?)", (row["id"], user_id, emoji))
    db.commit()
    message = one(db, row["id"], channel["server_id"])
    emit("reactions", {"id": row["id"], "reactions": message["reactions"]}, to=f"srv-{channel['id']}")
    if not have and row["sender_id"] != user_id:
        live.notify([row["sender_id"]], "server", f"{emoji} reaction in #{channel['name']}",
                    f"@{live.username(db, user_id)} reacted to your message", f"/servers/{channel['server_id']}/{channel['id']}?msg={row['id']}",
                    key=f"srv:{channel['id']}", mute=[("channel", channel["id"]), ("server", channel["server_id"])])


@socketio.on("srv_pin")
def on_pin(data):
    user_id = session.get("user_id")
    m, channel, row = reading(data, user_id)
    if row is None or "manage_messages" not in m["perms"]:
        return
    db = get_db()
    pinned = 0 if row["pinned"] else 1
    db.execute("UPDATE server_messages SET pinned = ? WHERE id = ?", (pinned, row["id"]))
    log_action(db, channel["server_id"], user_id, "message.pin" if pinned else "message.unpin", f"in #{channel['name']}")
    db.commit()
    emit("pinned", {"id": row["id"], "pinned": pinned}, to=f"srv-{channel['id']}")


@socketio.on("srv_vote")
def on_vote(data):
    user_id = session.get("user_id")
    m, channel, row = reading(data, user_id)
    choice = data.get("choice")
    if row is None or not row["embed"] or "react" not in m["perms"] or not isinstance(choice, int):
        return
    options = json.loads(row["embed"]).get("poll") or []
    if not 0 <= choice < len(options):
        return
    db = get_db()
    current = db.execute("SELECT choice FROM poll_votes WHERE message_id = ? AND user_id = ?", (row["id"], user_id)).fetchone()
    if current and current["choice"] == choice:
        db.execute("DELETE FROM poll_votes WHERE message_id = ? AND user_id = ?", (row["id"], user_id))
    else:
        db.execute("INSERT OR REPLACE INTO poll_votes (message_id, user_id, choice) VALUES (?, ?, ?)", (row["id"], user_id, choice))
    db.commit()
    message = one(db, row["id"], channel["server_id"])
    emit("votes", {"id": row["id"], "votes": message["votes"]}, to=f"srv-{channel['id']}")


@socketio.on("srv_thread")
def on_thread(data):
    """Start (or open) a thread under a message. The answer tells the browser where to go."""
    user_id = session.get("user_id")
    m, channel, row = reading(data, user_id)
    if row is None or channel["kind"] in ("thread", "forum") or "send" not in m["perms"] or m["timeout_until"]:
        return {"error": "You can't start a thread here."}
    db = get_db()
    existing = db.execute("SELECT id FROM server_channels WHERE parent_message_id = ? AND kind = 'thread'", (row["id"],)).fetchone()
    if existing:
        return {"thread_id": existing["id"]}
    name = " ".join(str(data.get("name") or row["body"] or "Thread").split())[:40] or "Thread"
    thread_id = db.execute(
        """INSERT INTO server_channels (server_id, name, kind, parent_id, parent_message_id, created_by)
           VALUES (?, ?, 'thread', ?, ?, ?)""", (channel["server_id"], name, channel["id"], row["id"], user_id)).lastrowid
    db.commit()
    emit("thread", {"id": row["id"], "thread_id": thread_id, "count": 0}, to=f"srv-{channel['id']}")
    return {"thread_id": thread_id}


@socketio.on("srv_seen")
def on_srv_seen(data):
    """The channel is open on someone's screen and a message arrived: it counts as read."""
    channel_id = data.get("channel_id")
    user_id = session.get("user_id")
    if user_id and isinstance(channel_id, int) and channel_for(channel_id, user_id)[1]:
        unread.mark_channel(channel_id, user_id)
        live.refresh_badges(user_id)
