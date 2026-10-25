"""Nova, ambiently available in every server channel: mention @nova and it answers, reading the recent
conversation for context. Unlike the Companions catalog (servers/companions.py), this needs no
installation -- it's one shared identity, lazily created once, usable anywhere."""
import re
import secrets

from werkzeug.security import generate_password_hash

MENTION = re.compile(r"(?<![\w@])@nova(?!\w)", re.I)


def nova_user_id(db):
    row = db.execute("SELECT id FROM users WHERE username = 'nova' AND is_bot = 1").fetchone()
    if row:
        return row["id"]
    user_id = db.execute(
        """INSERT INTO users (full_name, username, email, password_hash, bio, is_bot)
           VALUES ('Nova', 'nova', 'nova@bots.invalid', ?, 'The platform''s AI assistant.', 1)""",
        (generate_password_hash(secrets.token_hex(16)),)).lastrowid
    db.commit()
    return user_id


def history_text(db, channel):
    from servers.messages import recent
    lines = []
    for m in recent(db, channel, limit=15):
        text = m["body"] or ("[image]" if m.get("attach_type") == "image" else "[attachment]" if m.get("attachment") else "")
        if text:
            lines.append(f"{m['name']}: {text}"[:300])
    return "\n".join(lines[-15:])


def maybe_reply_in_channel(server_id, channel_id, asker_id, body):
    """Kicks off a background reply if `body` mentions @nova. Fire-and-forget: the human's own message
    has already been posted and returned to them by the time this runs."""
    if not MENTION.search(body):
        return
    from flask import current_app
    app = current_app._get_current_object()
    from extensions import socketio

    def work():
        with app.app_context():
            from db import get_db
            db = get_db()
            channel = db.execute("SELECT * FROM server_channels WHERE id = ?", (channel_id,)).fetchone()
            server = db.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
            if channel is None or server is None:
                return
            project_context = None
            if server["project_id"]:
                p = db.execute("SELECT title FROM projects WHERE id = ?", (server["project_id"],)).fetchone()
                project_context = p["title"] if p else None
            question = MENTION.sub("", body, count=1).strip() or "What do you make of this conversation?"
            from ai.gemini import ask_in_server
            answer = ask_in_server(db, question, project_context, history_text(db, channel))
            from servers.messages import post_message
            nova_id = nova_user_id(db)
            post_message(db, channel, nova_id, answer)
    socketio.start_background_task(work)


def dm_history_text(db, thread_id, limit=60):
    """As much of this DM's history as is practical to hand to the model (capped, not literally every
    message ever, to keep the prompt and the cost bounded)."""
    rows = db.execute(
        """SELECT u.full_name AS name, m.body, m.attach_type, m.attachment FROM dm_messages m
           JOIN users u ON u.id = m.sender_id WHERE m.thread_id = ? ORDER BY m.id DESC LIMIT ?""",
        (thread_id, limit)).fetchall()
    lines = []
    for m in reversed(rows):
        text = m["body"] or ("[image]" if m["attach_type"] == "image" else "[attachment]" if m["attachment"] else "")
        if text:
            lines.append(f"{m['name']}: {text}"[:300])
    return "\n".join(lines)


def maybe_reply_in_dm(thread_id, asker_id, body):
    """Mention @nova inside a DM and it answers right there, visible to both people -- reading as much of
    that thread's history as dm_history_text keeps."""
    if not MENTION.search(body):
        return
    from flask import current_app
    app = current_app._get_current_object()
    from extensions import socketio

    def work():
        with app.app_context():
            from db import get_db
            db = get_db()
            thread = db.execute("SELECT * FROM dm_threads WHERE id = ?", (thread_id,)).fetchone()
            if thread is None:
                return
            question = MENTION.sub("", body, count=1).strip() or "What do you make of this conversation?"
            from ai.gemini import ask_in_server
            answer = ask_in_server(db, question, None, dm_history_text(db, thread_id))
            nova_id = nova_user_id(db)
            cur = db.execute("INSERT INTO dm_messages (thread_id, sender_id, body) VALUES (?, ?, ?)",
                             (thread_id, nova_id, answer[:2000]))
            db.commit()
            from dms.routes import dm_message
            import live
            socketio.emit("message", dm_message(db, cur.lastrowid), to=f"dm-{thread_id}")
            live.inbox_changed(thread_id)
            live.notify([thread["user_a"], thread["user_b"]], "dm", "✨ Nova replied", answer[:140],
                        f"/messages/{thread_id}", key=f"dm:{thread_id}")
    socketio.start_background_task(work)
