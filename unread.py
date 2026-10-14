"""What is new for someone: unread chats, unread channels and new activity. Powers the red badges."""
from db import get_db


# ---------- marking things as read ----------

def mark_dm(thread_id, me):
    db = get_db()
    top = db.execute("SELECT COALESCE(MAX(id), 0) FROM dm_messages WHERE thread_id = ?", (thread_id,)).fetchone()[0]
    db.execute("""INSERT INTO dm_reads (thread_id, user_id, last_id) VALUES (?, ?, ?)
                  ON CONFLICT (thread_id, user_id) DO UPDATE SET last_id = MAX(last_id, excluded.last_id)""",
               (thread_id, me, top))
    db.commit()
    return top


def mark_channel(channel_id, me):
    db = get_db()
    top = db.execute("SELECT COALESCE(MAX(id), 0) FROM server_messages WHERE channel_id = ?", (channel_id,)).fetchone()[0]
    db.execute("""INSERT INTO channel_reads (channel_id, user_id, last_id) VALUES (?, ?, ?)
                  ON CONFLICT (channel_id, user_id) DO UPDATE SET last_id = MAX(last_id, excluded.last_id)""",
               (channel_id, me, top))
    db.commit()


def mark_server_read_from_now(server_id, me):
    """Someone who just joined shouldn't see the whole history as unread."""
    db = get_db()
    db.execute("""INSERT OR IGNORE INTO channel_reads (channel_id, user_id, last_id)
                  SELECT c.id, ?, COALESCE((SELECT MAX(id) FROM server_messages WHERE channel_id = c.id), 0)
                  FROM server_channels c WHERE c.server_id = ?""", (me, server_id))
    db.commit()


def mark_activity(me):
    db = get_db()
    db.execute("UPDATE users SET activity_seen = datetime('now','localtime') WHERE id = ?", (me,))
    db.commit()


# ---------- counting ----------

def dm_total(me):
    return get_db().execute(
        """SELECT COUNT(*) FROM dm_messages m JOIN dm_threads t ON t.id = m.thread_id
           LEFT JOIN dm_reads r ON r.thread_id = t.id AND r.user_id = :me
           WHERE :me IN (t.user_a, t.user_b) AND m.sender_id != :me AND m.id > COALESCE(r.last_id, 0)
             AND NOT (t.status = 'declined' AND t.requested_by != :me)
             AND NOT EXISTS (SELECT 1 FROM mutes WHERE user_id = :me AND kind = 'dm' AND target_id = t.id)""",
        {"me": me}).fetchone()[0]


def activity_total(me):
    db = get_db()
    seen = db.execute("SELECT COALESCE(activity_seen, '') FROM users WHERE id = ?", (me,)).fetchone()[0]
    args = {"me": me, "seen": seen}
    likes = db.execute(
        """SELECT COUNT(*) FROM likes l JOIN projects p ON p.id = l.post_id
           WHERE p.innovator_id = :me AND l.user_id != :me AND l.created_at > :seen""", args).fetchone()[0]
    comments = db.execute(
        """SELECT COUNT(*) FROM comments c JOIN projects p ON p.id = c.post_id
           WHERE p.innovator_id = :me AND c.user_id != :me AND c.created_at > :seen""", args).fetchone()[0]
    offers = db.execute(
        """SELECT COUNT(*) FROM interests i JOIN projects p ON p.id = i.project_id
           WHERE p.innovator_id = :me AND i.created_at > :seen""", args).fetchone()[0]
    answers = db.execute(
        """SELECT COUNT(*) FROM interests WHERE sponsor_id = :me AND status IN ('accepted', 'declined')
           AND responded_at > :seen""", args).fetchone()[0]
    return likes + comments + offers + answers


def server_counts(me):
    """({server_id: unread}, {channel_id: unread}) for the channels this person may read."""
    from servers.routes import membership, can_read, my_servers
    db = get_db()
    per_server, per_channel = {}, {}
    muted = {(r["kind"], r["target_id"]) for r in db.execute("SELECT kind, target_id FROM mutes WHERE user_id = ?", (me,))}
    for s in my_servers(me):
        m = membership(s["id"], me)
        if m is None or ("server", s["id"]) in muted:
            continue
        total = 0
        for c in db.execute("SELECT * FROM server_channels WHERE server_id = ?", (s["id"],)):
            if ("channel", c["id"]) in muted or not can_read(m, c):
                continue
            n = db.execute(
                """SELECT COUNT(*) FROM server_messages x
                   LEFT JOIN channel_reads r ON r.channel_id = x.channel_id AND r.user_id = :me
                   WHERE x.channel_id = :c AND x.sender_id != :me AND x.id > COALESCE(r.last_id, 0)""",
                {"me": me, "c": c["id"]}).fetchone()[0]
            if n:
                per_channel[c["id"]] = n
                total += n
        if total:
            per_server[s["id"]] = total
    return per_server, per_channel


def all_counts(me):
    per_server, per_channel = server_counts(me)
    return {"dms": dm_total(me), "activity": activity_total(me),
            "servers": per_server, "channels": per_channel, "server_total": sum(per_server.values())}
