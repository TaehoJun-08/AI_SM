"""The rest of a server: side panels (pins, threads, search, events, member cards), pitch-board posts,
events, custom emoji and managing companions."""
import re
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash, session, abort, jsonify
from db import get_db
from helpers import login_required
from media import save_attachment, delete_media
import live
from servers import companions
from servers.messages import post_message, recent, serialize, MSG_SELECT
from servers.routes import (servers_bp, membership, outranks, need, back, audit, can_read, can_write, channel_for,
                            valid_color)

EMOJI_NAME = re.compile(r"[A-Za-z0-9_]{2,20}")


# ---------- side panels: small HTML fragments the page slides in ----------

@servers_bp.route("/<int:server_id>/panel/<kind>")
@login_required
def panel(server_id, kind):
    m = need(server_id)
    db = get_db()
    ctx = {"kind": kind, "server": m["server"], "m": m}
    channel_id = request.args.get("channel", type=int)
    if kind in ("pins", "threads") and channel_id:
        _, channel = channel_for(channel_id, session["user_id"])
        if channel is None or channel["server_id"] != server_id:
            abort(404)
        ctx["channel"] = channel
        if kind == "pins":
            rows = db.execute(f"{MSG_SELECT} WHERE m.channel_id = :cid AND m.pinned = 1 ORDER BY m.id DESC",
                              {"cid": channel["id"], "sid": server_id}).fetchall()
            ctx["items"] = serialize(db, rows)
        else:
            ctx["items"] = db.execute(
                """SELECT t.id, t.name, u.username,
                     (SELECT COUNT(*) FROM server_messages WHERE channel_id = t.id) AS replies
                   FROM server_channels t LEFT JOIN users u ON u.id = t.created_by
                   WHERE t.parent_id = ? AND t.kind = 'thread' ORDER BY t.id DESC""", (channel["id"],)).fetchall()
    elif kind == "search":
        q = request.args.get("q", "").strip()
        ctx["q"] = q
        ctx["items"] = []
        if len(q) >= 2:
            like = "%" + q.replace("%", r"\%").replace("_", r"\_") + "%"
            rows = db.execute(
                f"""{MSG_SELECT} JOIN server_channels c ON c.id = m.channel_id
                    WHERE c.server_id = :sid AND m.body LIKE :like ESCAPE '\\' ORDER BY m.id DESC LIMIT 80""",
                {"sid": server_id, "like": like}).fetchall()
            readable = {}
            for r in rows:
                cid = r["channel_id"]
                if cid not in readable:
                    readable[cid] = db.execute("SELECT * FROM server_channels WHERE id = ?", (cid,)).fetchone()
                if can_read(m, readable[cid]):
                    ctx["items"].append(dict(r, channel_name=readable[cid]["name"]))
            ctx["items"] = ctx["items"][:40]
    elif kind == "events":
        ctx["events"] = db.execute(
            """SELECT e.*, u.username,
                 (SELECT COUNT(*) FROM event_rsvps WHERE event_id = e.id) AS going,
                 (SELECT 1 FROM event_rsvps WHERE event_id = e.id AND user_id = ?) AS mine
               FROM server_events e LEFT JOIN users u ON u.id = e.creator_id
               WHERE e.server_id = ? AND (e.notified = 0 OR e.starts_at >= datetime('now', 'localtime', '-1 day'))
               ORDER BY e.starts_at""", (session["user_id"], server_id)).fetchall()
        ctx["channels"] = [c for c in db.execute(
            "SELECT id, name FROM server_channels WHERE server_id = ? AND kind IN ('text','announcement','voice') ORDER BY position",
            (server_id,))]
    else:
        abort(404)
    return render_template("servers/_panel.html", **ctx)


@servers_bp.route("/<int:server_id>/member/<int:user_id>/card")
@login_required
def member_card(server_id, user_id):
    m = need(server_id)
    target = membership(server_id, user_id)
    if target is None:
        abort(404)
    db = get_db()
    person = db.execute("SELECT id, username, full_name, avatar, bio, is_bot FROM users WHERE id = ?", (user_id,)).fetchone()
    roles = db.execute("""SELECT r.name, r.color FROM member_roles mr JOIN server_roles r ON r.id = mr.role_id
                          WHERE mr.server_id = ? AND mr.user_id = ? ORDER BY r.position DESC""", (server_id, user_id)).fetchall()
    joined = db.execute("SELECT joined_at FROM server_members WHERE server_id = ? AND user_id = ?", (server_id, user_id)).fetchone()[0]
    acts = {} if person["is_bot"] or user_id == session["user_id"] else {
        "kick": "kick" in m["perms"] and outranks(m, target), "ban": "ban" in m["perms"] and outranks(m, target),
        "timeout": "timeout" in m["perms"] and outranks(m, target)}
    from urllib.parse import urlparse
    next_url = urlparse(request.referrer or "").path
    return render_template("servers/_card.html", next_url=next_url, p=person, roles=roles, joined=joined, target=target, acts=acts,
                           server=m["server"], me=session["user_id"], m=m)


# ---------- pitch board (forum channel) ----------

@servers_bp.route("/<int:server_id>/channels/<int:channel_id>/pitch", methods=["POST"])
@login_required
def new_pitch(server_id, channel_id):
    me = session["user_id"]
    m, forum = channel_for(channel_id, me)
    if forum is None or forum["server_id"] != server_id or forum["kind"] != "forum" or "send" not in m["perms"] or m["timeout_until"]:
        abort(404)
    title = " ".join(request.form.get("title", "").split())[:80]
    body = request.form.get("body", "").strip()[:1500]
    ask = request.form.get("ask", type=int)
    if not title or not body:
        flash("A pitch needs a title and a few words about it.")
        return redirect(url_for("servers.view", server_id=server_id, channel_id=channel_id))
    db = get_db()
    thread_id = db.execute(
        """INSERT INTO server_channels (server_id, name, kind, parent_id, created_by) VALUES (?, ?, 'thread', ?, ?)""",
        (server_id, title, forum["id"], me)).lastrowid
    thread = db.execute("SELECT * FROM server_channels WHERE id = ?", (thread_id,)).fetchone()
    embed = {"kind": "pitch", "title": title, "description": body, "color": "#f59e0b",
             "footer": f"Pitched by @{live.username(db, me)}"}
    if ask and ask > 0:
        embed["fields"] = [{"name": "Looking for", "value": f"₹{ask:,}", "inline": True}]
    post_message(db, thread, me, "", embed=embed, silent=True)
    live.notify_channel(db, forum, me, f"New pitch: {title}")
    return redirect(url_for("servers.view", server_id=server_id, channel_id=thread_id))


# ---------- events ----------

@servers_bp.route("/<int:server_id>/events", methods=["POST"])
@login_required
def add_event(server_id):
    need(server_id, "manage_events")
    title = request.form.get("title", "").strip()[:80]
    when = request.form.get("starts_at", "")
    try:
        starts = datetime.strptime(when, "%Y-%m-%dT%H:%M")
    except ValueError:
        starts = None
    ajax = bool(request.form.get("ajax"))
    if not title or starts is None:
        flash("An event needs a title and a start time.")
        return jsonify(ok=False) if ajax else redirect(request.referrer or url_for("servers.view", server_id=server_id))
    channel_id = request.form.get("channel_id", type=int)
    db = get_db()
    if channel_id and not db.execute("SELECT 1 FROM server_channels WHERE id = ? AND server_id = ?", (channel_id, server_id)).fetchone():
        channel_id = None
    db.execute("""INSERT INTO server_events (server_id, creator_id, title, description, starts_at, channel_id)
                  VALUES (?, ?, ?, ?, ?, ?)""", (server_id, session["user_id"], title,
               request.form.get("description", "").strip()[:300], starts.strftime("%Y-%m-%d %H:%M:00"), channel_id))
    audit(server_id, "event.create", title)
    db.commit()
    if ajax:
        return jsonify(ok=True)
    flash("Event created. 📅")
    return redirect(request.referrer or url_for("servers.view", server_id=server_id))


@servers_bp.route("/<int:server_id>/events/<int:event_id>/rsvp", methods=["POST"])
@login_required
def rsvp(server_id, event_id):
    need(server_id)
    db = get_db()
    if not db.execute("SELECT 1 FROM server_events WHERE id = ? AND server_id = ?", (event_id, server_id)).fetchone():
        abort(404)
    me = session["user_id"]
    if db.execute("DELETE FROM event_rsvps WHERE event_id = ? AND user_id = ?", (event_id, me)).rowcount == 0:
        db.execute("INSERT INTO event_rsvps (event_id, user_id) VALUES (?, ?)", (event_id, me))
    db.commit()
    return jsonify(ok=True)


@servers_bp.route("/<int:server_id>/events/<int:event_id>/delete", methods=["POST"])
@login_required
def delete_event(server_id, event_id):
    need(server_id, "manage_events")
    db = get_db()
    db.execute("DELETE FROM server_events WHERE id = ? AND server_id = ?", (event_id, server_id))
    audit(server_id, "event.delete", str(event_id))
    db.commit()
    return jsonify(ok=True)


# ---------- custom emoji ----------

@servers_bp.route("/<int:server_id>/emoji", methods=["POST"])
@login_required
def add_emoji(server_id):
    need(server_id, "manage_server")
    name = request.form.get("name", "").strip().strip(":")
    file = request.files.get("image")
    if not EMOJI_NAME.fullmatch(name) or file is None or not file.filename:
        flash("Give the emoji a name (letters, numbers, _) and a picture.")
        return back(server_id, "emoji")
    db = get_db()
    if db.execute("SELECT COUNT(*) FROM server_emojis WHERE server_id = ?", (server_id,)).fetchone()[0] >= 50:
        flash("A server can have up to 50 custom emoji.")
        return back(server_id, "emoji")
    file.stream.seek(0, 2)
    size = file.stream.tell()
    file.stream.seek(0)
    try:
        path, kind, _ = save_attachment(file)
    except ValueError as e:
        flash(str(e))
        return back(server_id, "emoji")
    if kind != "image" or size > 512 * 1024:
        delete_media(path)
        flash("Emoji must be a picture under 512 KB.")
        return back(server_id, "emoji")
    if db.execute("INSERT OR IGNORE INTO server_emojis (server_id, name, path) VALUES (?, ?, ?)", (server_id, name, path)).rowcount == 0:
        delete_media(path)
        flash(f":{name}: already exists.")
    else:
        audit(server_id, "emoji.add", f":{name}:")
        db.commit()
        flash(f"Added :{name}:")
    return back(server_id, "emoji")


@servers_bp.route("/<int:server_id>/emoji/<int:emoji_id>/delete", methods=["POST"])
@login_required
def delete_emoji(server_id, emoji_id):
    need(server_id, "manage_server")
    db = get_db()
    row = db.execute("SELECT * FROM server_emojis WHERE id = ? AND server_id = ?", (emoji_id, server_id)).fetchone()
    if row:
        db.execute("DELETE FROM server_emojis WHERE id = ?", (emoji_id,))
        audit(server_id, "emoji.delete", f":{row['name']}:")
        db.commit()
        delete_media(row["path"])
    return back(server_id, "emoji")


# ---------- managing companions ----------

def own_bot(server_id, bot_id):
    row = get_db().execute("SELECT * FROM bots WHERE id = ? AND server_id = ?", (bot_id, server_id)).fetchone()
    if row is None:
        abort(404)
    return row


@servers_bp.route("/<int:server_id>/companions", methods=["POST"])
@login_required
def add_companion(server_id):
    need(server_id, "manage_companions")
    kind = request.form.get("kind", "")
    db = get_db()
    if kind in companions.CATALOG and not companions.bots_of(db, server_id, kind):
        companions.install(db, server_id, kind, session["user_id"])
        audit(server_id, "companion.add", companions.CATALOG[kind]["name"])
        db.commit()
        flash(f"{companions.CATALOG[kind]['name']} joined the server.")
    elif kind in ("custom", "webhook"):
        name = request.form.get("name", "").strip()[:30]
        if not name:
            flash("Give your companion a name.")
            return back(server_id, "companions")
        channel_id = request.form.get("channel_id", type=int)
        if kind == "webhook" and not db.execute("SELECT 1 FROM server_channels WHERE id = ? AND server_id = ? AND kind IN ('text','announcement')",
                                                (channel_id, server_id)).fetchone():
            flash("Pick the channel the webhook posts into.")
            return back(server_id, "companions")
        url = request.form.get("interaction_url", "").strip()[:300]
        if url and not companions.safe_url(url):
            flash("The interaction URL must be a normal http(s) address.")
            return back(server_id, "companions")
        bot_id, token = companions.install(db, server_id, kind, session["user_id"], name=name,
                                           channel_id=channel_id if kind == "webhook" else None, url=url)
        audit(server_id, f"{kind}.add", name)
        db.commit()
        session["new_token"] = {"bot": bot_id, "token": token, "kind": kind}
        flash(f"{name} created. Copy its token now — it is shown only once.")
    return back(server_id, "companions")


@servers_bp.route("/<int:server_id>/companions/<int:bot_id>/remove", methods=["POST"])
@login_required
def remove_companion(server_id, bot_id):
    need(server_id, "manage_companions")
    bot = own_bot(server_id, bot_id)
    companions.remove(get_db(), bot_id, server_id)
    audit(server_id, "companion.remove", bot["name"])
    get_db().commit()
    flash(f"{bot['name']} was removed.")
    return back(server_id, "companions")


@servers_bp.route("/<int:server_id>/companions/<int:bot_id>/config", methods=["POST"])
@login_required
def config_companion(server_id, bot_id):
    need(server_id, "manage_companions")
    bot = own_bot(server_id, bot_id)
    db = get_db()
    if bot["kind"] in companions.CATALOG:
        cfg = {}
        for key, _, kind, default, _ in companions.CATALOG[bot["kind"]]["config"]:
            cfg[key] = ("1" if request.form.get(key) else "0") if kind == "check" else request.form.get(key, default).strip()[:800]
        db.execute("UPDATE bots SET config = ? WHERE id = ?", (__import__("json").dumps(cfg), bot_id))
    else:
        url = request.form.get("interaction_url", "").strip()[:300]
        if url and not companions.safe_url(url):
            flash("The interaction URL must be a normal http(s) address.")
            return back(server_id, "companions")
        name = request.form.get("name", "").strip()[:30] or bot["name"]
        db.execute("UPDATE bots SET interaction_url = ?, name = ? WHERE id = ?", (url, name, bot_id))
        db.execute("UPDATE users SET full_name = ? WHERE id = ?", (name, bot["user_id"]))
    audit(server_id, "companion.config", bot["name"])
    db.commit()
    flash("Saved.")
    return back(server_id, "companions")


@servers_bp.route("/<int:server_id>/companions/<int:bot_id>/token", methods=["POST"])
@login_required
def new_token(server_id, bot_id):
    need(server_id, "manage_companions")
    bot = own_bot(server_id, bot_id)
    if bot["kind"] not in ("custom", "webhook"):
        abort(404)
    import secrets
    token = secrets.token_urlsafe(32)
    db = get_db()
    db.execute("UPDATE bots SET token_hash = ? WHERE id = ?", (companions.token_hash(token), bot_id))
    audit(server_id, "companion.token", bot["name"])
    db.commit()
    session["new_token"] = {"bot": bot_id, "token": token, "kind": bot["kind"]}
    flash("New token created. The old one stopped working.")
    return back(server_id, "companions")
