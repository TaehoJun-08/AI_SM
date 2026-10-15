"""Everything about turning a channel message into rows and live pushes: one place that stores a
message, tells the room, and notifies people. People (chat.py), companions (companions.py) and
the bot API (botapi.py) all post through post_message()."""
import json
import re
from extensions import socketio
import live

MSG_SELECT = """
    SELECT m.id, m.channel_id, m.body, m.created_at, m.sender_id,
      COALESCE(sm.nickname, CASE WHEN u.is_bot THEN u.full_name ELSE u.username END) AS name,
      u.full_name, u.avatar, u.is_bot, u.username,
      m.attachment, m.attach_type, m.attach_name, m.edited, m.reply_to, m.pinned, m.embed,
      COALESCE(rsm.nickname, CASE WHEN ru.is_bot THEN ru.full_name ELSE ru.username END) AS reply_name,
      COALESCE(NULLIF(rm.body, ''), CASE rm.attach_type WHEN 'image' THEN '📷 Photo' WHEN 'video' THEN '🎥 Video' WHEN 'audio' THEN '🎵 Audio' ELSE '📎 File' END) AS reply_text,
      (SELECT r.color FROM member_roles mr JOIN server_roles r ON r.id = mr.role_id
       WHERE mr.server_id = :sid AND mr.user_id = m.sender_id
       ORDER BY r.position DESC, r.id LIMIT 1) AS color,
      (SELECT t.id FROM server_channels t WHERE t.parent_message_id = m.id AND t.kind = 'thread') AS thread_id,
      (SELECT COUNT(*) FROM server_messages tm JOIN server_channels t ON t.id = tm.channel_id
       WHERE t.parent_message_id = m.id AND t.kind = 'thread') AS thread_count
    FROM server_messages m JOIN users u ON u.id = m.sender_id
    LEFT JOIN server_members sm ON sm.server_id = :sid AND sm.user_id = m.sender_id
    LEFT JOIN server_messages rm ON rm.id = m.reply_to
    LEFT JOIN users ru ON ru.id = rm.sender_id
    LEFT JOIN server_members rsm ON rsm.server_id = :sid AND rsm.user_id = rm.sender_id
"""

MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9_.]{1,30})")
HEX = re.compile(r"#[0-9a-fA-F]{6}")
URL = re.compile(r"(https?://[^\s]{1,500}|/(?!/)[^\s]{0,500})")


def valid_reply(db, channel_id, reply_id):
    """The id to store as reply_to: only a message from this same channel counts."""
    if not isinstance(reply_id, int):
        return None
    row = db.execute("SELECT id FROM server_messages WHERE id = ? AND channel_id = ?", (reply_id, channel_id)).fetchone()
    return row["id"] if row else None


def serialize(db, rows):
    """Message rows -> dicts that also carry reactions, poll votes and a parsed embed."""
    rows = [dict(r) for r in rows]
    ids = [r["id"] for r in rows]
    if not ids:
        return rows
    marks = ",".join("?" * len(ids))
    reactions, votes = {}, {}
    for r in db.execute(f"SELECT message_id, emoji, user_id FROM message_reactions WHERE message_id IN ({marks}) ORDER BY rowid", ids):
        reactions.setdefault(r["message_id"], {}).setdefault(r["emoji"], []).append(r["user_id"])
    for r in db.execute(f"SELECT message_id, user_id, choice FROM poll_votes WHERE message_id IN ({marks})", ids):
        votes.setdefault(r["message_id"], {})[r["user_id"]] = r["choice"]
    for r in rows:
        r["reactions"] = [{"e": e, "u": u} for e, u in reactions.get(r["id"], {}).items()]
        r["votes"] = votes.get(r["id"], {})
        r["embed"] = json.loads(r["embed"]) if r["embed"] else None
    return rows


def one(db, message_id, server_id):
    row = db.execute(f"{MSG_SELECT} WHERE m.id = :mid", {"mid": message_id, "sid": server_id}).fetchone()
    return serialize(db, [row])[0] if row else None


def recent(db, channel, before=None, upto=None, limit=50):
    """The last `limit` messages of a channel (or the ones around/before a given id)."""
    where, params = "m.channel_id = :cid", {"cid": channel["id"], "sid": channel["server_id"]}
    if upto:
        where += " AND m.id <= :upto"
        params["upto"] = upto
    rows = db.execute(f"SELECT * FROM ({MSG_SELECT} WHERE {where} ORDER BY m.id DESC LIMIT {int(limit)}) ORDER BY id",
                      params).fetchall()
    return serialize(db, rows)


def clean_embed(embed):
    """Keep only the parts of an embed we know how to draw. Returns a JSON string or None."""
    if not isinstance(embed, dict):
        return None
    out = {}
    for key, limit in (("title", 256), ("description", 2000), ("footer", 100)):
        if isinstance(embed.get(key), str) and embed[key].strip():
            out[key] = embed[key].strip()[:limit]
    if isinstance(embed.get("color"), str) and HEX.fullmatch(embed["color"]):
        out["color"] = embed["color"]
    for key in ("url", "image"):
        if isinstance(embed.get(key), str) and URL.fullmatch(embed[key]):
            out[key] = embed[key]
    fields = []
    for f in (embed.get("fields") or [])[:10]:
        if isinstance(f, dict) and isinstance(f.get("name"), str) and isinstance(f.get("value"), (str, int, float)):
            fields.append({"name": f["name"][:60], "value": str(f["value"])[:300], "inline": bool(f.get("inline"))})
    if fields:
        out["fields"] = fields
    progress = embed.get("progress")
    if isinstance(progress, dict) and isinstance(progress.get("value"), (int, float)) \
            and isinstance(progress.get("goal"), (int, float)) and progress["goal"] > 0:
        out["progress"] = {"value": progress["value"], "goal": progress["goal"],
                           "label": str(progress.get("label", ""))[:60]}
    poll = embed.get("poll")
    if isinstance(poll, list):
        options = [str(o).strip()[:80] for o in poll if str(o).strip()][:8]
        if len(options) >= 2:
            out["poll"] = options
    if embed.get("kind") in ("pitch", "project", "poll"):
        out["kind"] = embed["kind"]
    return json.dumps(out) if out else None


def post_message(db, channel, sender_id, body="", *, reply_to=None, attachment=None, attach_type=None,
                 attach_name=None, embed=None, can_ping_all=False, silent=False):
    """Store a message in a channel, push it to everyone looking at the channel, and notify people.
    Returns the message dict."""
    bot = db.execute("SELECT config FROM bots WHERE user_id = ?", (sender_id,)).fetchone()
    if bot:
        only = json.loads(bot["config"] or "{}").get("only") or []
        if only and channel["id"] not in only and (channel["parent_id"] or 0) not in only:
            return None
    embed_json = clean_embed(embed)
    body = (body or "").strip()[:2000]
    if not body and not attachment:
        if embed_json:
            body = json.loads(embed_json).get("title") or "Embed"
        else:
            return None
    cur = db.execute(
        """INSERT INTO server_messages (channel_id, sender_id, body, reply_to, attachment, attach_type, attach_name, embed)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (channel["id"], sender_id, body, reply_to, attachment, attach_type, attach_name, embed_json))
    db.commit()
    message = one(db, cur.lastrowid, channel["server_id"])
    socketio.emit("message", message, to=f"srv-{channel['id']}")
    if channel["kind"] == "thread" and channel["parent_id"]:      # the "N replies" chip on the starting message
        count = db.execute("SELECT COUNT(*) FROM server_messages WHERE channel_id = ?", (channel["id"],)).fetchone()[0]
        socketio.emit("thread", {"id": channel["parent_message_id"], "thread_id": channel["id"], "count": count},
                      to=f"srv-{channel['parent_id']}")
    if not silent:
        pinged = notify_mentions(db, channel, sender_id, body, can_ping_all)
        live.notify_channel(db, channel, sender_id, live.preview(body, attach_type), skip=pinged)
    return message


def people_mentioned(db, server_id, body, can_ping_all):
    """User ids named in a message: @username, @RoleName, and @everyone / @here (if allowed)."""
    ids = set()
    for name in set(MENTION.findall(body)):
        if name in ("everyone", "here"):
            if can_ping_all:
                ids.update(r["user_id"] for r in db.execute(
                    "SELECT user_id FROM server_members WHERE server_id = ?", (server_id,)))
            continue
        row = db.execute("""SELECT u.id FROM users u JOIN server_members sm ON sm.user_id = u.id
                            WHERE sm.server_id = ? AND u.username = ?""", (server_id, name)).fetchone()
        if row:
            ids.add(row["id"])
        ids.update(r["user_id"] for r in db.execute(
            """SELECT mr.user_id FROM member_roles mr JOIN server_roles r ON r.id = mr.role_id
               WHERE mr.server_id = ? AND r.name = ? COLLATE NOCASE""", (server_id, name)))
    return ids


def notify_mentions(db, channel, sender_id, body, can_ping_all):
    from servers.routes import membership, can_read
    if "@" not in body:
        return set()
    people = set()
    for uid in people_mentioned(db, channel["server_id"], body, can_ping_all) - {sender_id}:
        m = membership(channel["server_id"], uid)
        if m and not m["pending_rules"] and can_read(m, channel):
            people.add(uid)
    if people:
        server = db.execute("SELECT name FROM servers WHERE id = ?", (channel["server_id"],)).fetchone()
        live.notify(people, "server", f"🔔 Mentioned in #{channel['name']} · {server['name']}",
                    f"@{live.username(db, sender_id)}: {body}", f"/servers/{channel['server_id']}/{channel['id']}",
                    key=f"srv:{channel['id']}", mute=[("server", channel["server_id"])])
    return people


def tell_only(user_id, channel_id, text, embed=None):
    """A reply only one person sees (like a bot's "you can't do that"). Nothing is stored."""
    socketio.emit("ephemeral", {"channel_id": channel_id, "text": text, "embed": json.loads(clean_embed(embed)) if embed and clean_embed(embed) else None},
                  to=f"user-{user_id}")
