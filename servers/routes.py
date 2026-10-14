import re
import secrets
from flask import (Blueprint, render_template, request, g,
                   redirect, url_for, flash, session, abort, jsonify)
from db import get_db
from helpers import login_required
from extensions import socketio
from chatmedia import collect
import live
import unread

servers_bp = Blueprint("servers", __name__, url_prefix="/servers")

@servers_bp.before_request
def remember_members():
    """Note who is in the server before a change, so people who get removed hear about it too."""
    server_id = (request.view_args or {}).get("server_id")
    if request.method == "POST" and server_id:
        g.live_before = [r["user_id"] for r in get_db().execute(
            "SELECT user_id FROM server_members WHERE server_id = ?", (server_id,))]


@servers_bp.after_request
def announce_changes(response):
    """Any successful change to a server (channels, roles, members, name...) redraws its open pages."""
    server_id = (request.view_args or {}).get("server_id") or g.get("live_server")
    if request.method == "POST" and response.status_code < 400:
        live.touch(["servers"])
        if server_id:
            live.server_changed(server_id, g.get("live_before", ()))
    return response


COLOR = re.compile(r"#[0-9a-fA-F]{6}")
CHANNEL_NAME = re.compile(r"[a-z0-9_-]{1,30}")
TABS = ["overview", "roles", "channels", "members", "invite"]

MSG_SELECT = """
    SELECT m.id, m.body, m.created_at, m.sender_id, u.username AS name, u.full_name, u.avatar,
      m.attachment, m.attach_type, m.attach_name, m.edited, m.reply_to, ru.username AS reply_name,
      COALESCE(NULLIF(rm.body, ''), CASE rm.attach_type WHEN 'image' THEN '📷 Photo' WHEN 'video' THEN '🎥 Video' WHEN 'audio' THEN '🎵 Audio' ELSE '📎 File' END) AS reply_text,
      (SELECT r.color FROM member_roles mr JOIN server_roles r ON r.id = mr.role_id
       WHERE mr.server_id = :sid AND mr.user_id = m.sender_id
       ORDER BY r.position DESC, r.id LIMIT 1) AS color
    FROM server_messages m JOIN users u ON u.id = m.sender_id
    LEFT JOIN server_messages rm ON rm.id = m.reply_to LEFT JOIN users ru ON ru.id = rm.sender_id
"""


def valid_reply(db, channel_id, reply_id):
    """The id to store as reply_to: only a message from this same channel counts."""
    if not isinstance(reply_id, int):
        return None
    row = db.execute("SELECT id FROM server_messages WHERE id = ? AND channel_id = ?", (reply_id, channel_id)).fetchone()
    return row["id"] if row else None


# ---------- permissions ----------

def membership(server_id, user_id):
    """None if the user isn't in the server, else a dict describing what they may do."""
    db = get_db()
    server = db.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
    if server is None or not db.execute(
            "SELECT 1 FROM server_members WHERE server_id = ? AND user_id = ?",
            (server_id, user_id)).fetchone():
        return None
    roles = db.execute(
        """SELECT r.* FROM member_roles m JOIN server_roles r ON r.id = m.role_id
           WHERE m.server_id = ? AND m.user_id = ? ORDER BY r.position DESC, r.id""",
        (server_id, user_id)).fetchall()
    is_owner = server["owner_id"] == user_id
    return {"server": server, "is_owner": is_owner,
            "is_manager": is_owner or any(r["is_admin"] for r in roles),
            "role_ids": {r["id"] for r in roles}}


def can_read(m, channel):
    if not channel["is_private"] or m["is_manager"]:
        return True
    allowed = {r["role_id"] for r in get_db().execute(
        "SELECT role_id FROM channel_roles WHERE channel_id = ?", (channel["id"],))}
    return bool(allowed & m["role_ids"])


def can_write(m, channel):
    return can_read(m, channel) and (channel["kind"] == "text" or m["is_manager"])


def channel_for(channel_id, user_id):
    """(membership, channel) if the user may read that channel, otherwise (None, None)."""
    channel = get_db().execute("SELECT * FROM server_channels WHERE id = ?",
                               (channel_id,)).fetchone()
    if channel is None:
        return None, None
    m = membership(channel["server_id"], user_id)
    if m is None or not can_read(m, channel):
        return None, None
    return m, channel


def manager_or_404(server_id):
    m = membership(server_id, session["user_id"])
    if m is None or not m["is_manager"]:
        abort(404)
    return m


def back(server_id, tab):
    return redirect(url_for("servers.settings", server_id=server_id, tab=tab))


# ---------- shared page data ----------

def my_servers(user_id):
    return get_db().execute(
        """SELECT s.id, s.name, s.icon, s.color FROM servers s
           JOIN server_members m ON m.server_id = s.id
           WHERE m.user_id = ? ORDER BY m.joined_at, s.id""", (user_id,)).fetchall()


def create_server(user_id, name, icon, color, description, is_public):
    db = get_db()
    cur = db.execute(
        """INSERT INTO servers (owner_id, name, icon, color, description, invite_code, is_public)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, name, icon, color, description, secrets.token_hex(4).upper(), is_public))
    sid = cur.lastrowid
    db.execute("INSERT INTO server_members (server_id, user_id) VALUES (?, ?)", (sid, user_id))
    info = db.execute("INSERT INTO server_categories (server_id, name, position) VALUES (?, 'Information', 0)",
                      (sid,)).lastrowid
    text = db.execute("INSERT INTO server_categories (server_id, name, position) VALUES (?, 'Text Channels', 1)",
                      (sid,)).lastrowid
    db.execute("""INSERT INTO server_channels (server_id, category_id, name, kind, topic, position)
                  VALUES (?, ?, 'announcements', 'announcement', 'Official news from the owners', 0)""",
               (sid, info))
    db.execute("""INSERT INTO server_channels (server_id, category_id, name, topic, position)
                  VALUES (?, ?, 'general', 'Say hi to everyone', 0)""", (sid, text))
    db.commit()
    return sid


def valid_color(value, fallback):
    return value if COLOR.fullmatch(value or "") else fallback


# ---------- pages ----------

@servers_bp.route("/")
@login_required
def home():
    me = session["user_id"]
    explore = get_db().execute(
        """SELECT s.*, (SELECT COUNT(*) FROM server_members WHERE server_id = s.id) AS members
           FROM servers s WHERE s.is_public = 1
             AND s.id NOT IN (SELECT server_id FROM server_members WHERE user_id = ?)
           ORDER BY members DESC, s.id DESC LIMIT 24""", (me,)).fetchall()
    return render_template("servers/home.html", rail=my_servers(me), server=None, explore=explore)


@servers_bp.route("/<int:server_id>")
@servers_bp.route("/<int:server_id>/<int:channel_id>")
@login_required
def view(server_id, channel_id=None):
    me = session["user_id"]
    m = membership(server_id, me)
    if m is None:
        abort(404)
    db = get_db()
    server = m["server"]
    if channel_id is not None:
        unread.mark_channel(channel_id, me)
    categories = db.execute("SELECT * FROM server_categories WHERE server_id = ? ORDER BY position, id",
                            (server_id,)).fetchall()
    channels = [c for c in db.execute(
        "SELECT * FROM server_channels WHERE server_id = ? ORDER BY position, id", (server_id,))
        if can_read(m, c)]
    groups = [{"category": None, "channels": [c for c in channels if c["category_id"] is None]}]
    groups += [{"category": cat, "channels": [c for c in channels if c["category_id"] == cat["id"]]}
               for cat in categories]
    groups = [g for g in groups if g["channels"] or (g["category"] and m["is_manager"])]
    current = next((c for c in channels if c["id"] == channel_id), None)
    if channel_id is not None and current is None:
        abort(404)
    if current is None and channels:
        current = next(c for g in groups for c in g["channels"])
        unread.mark_channel(current["id"], me)
    messages = []
    if current:
        messages = db.execute(
            f"SELECT * FROM ({MSG_SELECT} WHERE m.channel_id = :cid ORDER BY m.id DESC LIMIT 50) ORDER BY id",
            {"cid": current["id"], "sid": server_id}).fetchall()
    people = db.execute(
        """SELECT u.id, u.username, u.full_name, u.avatar,
             (SELECT r.color FROM member_roles mr JOIN server_roles r ON r.id = mr.role_id
              WHERE mr.server_id = :sid AND mr.user_id = u.id
              ORDER BY r.position DESC, r.id LIMIT 1) AS color,
             (SELECT r.name FROM member_roles mr JOIN server_roles r ON r.id = mr.role_id
              WHERE mr.server_id = :sid AND mr.user_id = u.id
              ORDER BY r.position DESC, r.id LIMIT 1) AS top_role
           FROM server_members sm JOIN users u ON u.id = sm.user_id
           WHERE sm.server_id = :sid ORDER BY u.username COLLATE NOCASE""", {"sid": server_id}).fetchall()
    member_groups = {}
    for p in people:
        label = "Owner" if p["id"] == server["owner_id"] else (p["top_role"] or "Members")
        member_groups.setdefault(label, []).append(p)
    order = ["Owner"] + [r["name"] for r in db.execute(
        "SELECT name FROM server_roles WHERE server_id = ? ORDER BY position DESC, id", (server_id,))]
    member_groups = [(label, member_groups[label]) for label in order + ["Members"]
                     if label in member_groups]
    return render_template("servers/view.html", rail=my_servers(me), server=server, m=m,
                           groups=groups, current=current, messages=messages,
                           member_groups=member_groups, member_count=len(people),
                           writable=bool(current) and can_write(m, current))


@servers_bp.route("/create", methods=["POST"])
@login_required
def create():
    name = request.form.get("name", "").strip()
    if not 1 <= len(name) <= 40:
        flash("Give your server a name (up to 40 characters).")
        return redirect(url_for("servers.home"))
    sid = create_server(session["user_id"], name, request.form.get("icon", "").strip()[:4],
                        valid_color(request.form.get("color"), "#5865f2"),
                        request.form.get("description", "").strip()[:200],
                        0 if request.form.get("private") else 1)
    flash(f"{name} is ready. Make it yours in Server Settings. ✨")
    return redirect(url_for("servers.view", server_id=sid))


@servers_bp.route("/join", methods=["POST"])
@login_required
def join_code():
    code = request.form.get("code", "").strip().upper()
    server = get_db().execute("SELECT id FROM servers WHERE invite_code = ?", (code,)).fetchone()
    if server is None:
        flash("That invite code doesn't exist.")
        return redirect(url_for("servers.home"))
    return join_server(server["id"])


@servers_bp.route("/<int:server_id>/join", methods=["POST"])
@login_required
def join_public(server_id):
    server = get_db().execute("SELECT is_public FROM servers WHERE id = ?", (server_id,)).fetchone()
    if server is None or not server["is_public"]:
        abort(404)
    return join_server(server_id)


def join_server(server_id):
    db = get_db()
    joined = db.execute("INSERT OR IGNORE INTO server_members (server_id, user_id) VALUES (?, ?)",
                        (server_id, session["user_id"])).rowcount
    db.commit()
    if joined:
        unread.mark_server_read_from_now(server_id, session["user_id"])
        server = db.execute("SELECT owner_id, name FROM servers WHERE id = ?", (server_id,)).fetchone()
        live.notify([server["owner_id"]], "server", "New member",
                    f"@{live.username(db, session['user_id'])} joined {server['name']}", f"/servers/{server_id}")
    g.live_server = server_id
    return redirect(url_for("servers.view", server_id=server_id))


@servers_bp.route("/<int:server_id>/leave", methods=["POST"])
@login_required
def leave(server_id):
    m = membership(server_id, session["user_id"])
    if m is None:
        abort(404)
    if m["is_owner"]:
        flash("Owners can't leave. Delete the server instead.")
        return back(server_id, "overview")
    db = get_db()
    db.execute("DELETE FROM server_members WHERE server_id = ? AND user_id = ?",
               (server_id, session["user_id"]))
    db.commit()
    flash(f"You left {m['server']['name']}.")
    return redirect(url_for("servers.home"))


@servers_bp.route("/<int:server_id>/delete", methods=["POST"])
@login_required
def delete(server_id):
    m = membership(server_id, session["user_id"])
    if m is None or not m["is_owner"]:
        abort(404)
    if request.form.get("confirm", "").strip() != m["server"]["name"]:
        flash("Type the server's name exactly to delete it.")
        return back(server_id, "overview")
    db = get_db()
    db.execute("DELETE FROM servers WHERE id = ?", (server_id,))
    db.commit()
    flash("Server deleted.")
    return redirect(url_for("servers.home"))


# ---------- server settings ----------

@servers_bp.route("/<int:server_id>/settings")
@login_required
def settings(server_id):
    m = manager_or_404(server_id)
    tab = request.args.get("tab", "overview")
    if tab not in TABS:
        tab = "overview"
    db = get_db()
    roles = db.execute("SELECT * FROM server_roles WHERE server_id = ? ORDER BY position DESC, id",
                       (server_id,)).fetchall()
    categories = db.execute("SELECT * FROM server_categories WHERE server_id = ? ORDER BY position, id",
                            (server_id,)).fetchall()
    channels = db.execute("SELECT * FROM server_channels WHERE server_id = ? ORDER BY position, id",
                          (server_id,)).fetchall()
    channel_roles = {}
    for row in db.execute("""SELECT cr.channel_id, cr.role_id FROM channel_roles cr
                             JOIN server_channels c ON c.id = cr.channel_id WHERE c.server_id = ?""",
                          (server_id,)):
        channel_roles.setdefault(row["channel_id"], set()).add(row["role_id"])
    members = db.execute(
        """SELECT u.id, u.username, u.full_name, u.avatar FROM server_members sm
           JOIN users u ON u.id = sm.user_id WHERE sm.server_id = ?
           ORDER BY u.username COLLATE NOCASE""", (server_id,)).fetchall()
    member_roles = {}
    for row in db.execute("SELECT user_id, role_id FROM member_roles WHERE server_id = ?", (server_id,)):
        member_roles.setdefault(row["user_id"], set()).add(row["role_id"])
    return render_template("servers/settings.html", rail=my_servers(session["user_id"]),
                           server=m["server"], m=m, tab=tab, tabs=TABS, roles=roles,
                           categories=categories, channels=channels, channel_roles=channel_roles,
                           members=members, member_roles=member_roles)


@servers_bp.route("/<int:server_id>/settings/overview", methods=["POST"])
@login_required
def save_overview(server_id):
    m = manager_or_404(server_id)
    name = request.form.get("name", "").strip()
    if not 1 <= len(name) <= 40:
        flash("Server names are 1-40 characters.")
        return back(server_id, "overview")
    db = get_db()
    db.execute("""UPDATE servers SET name = ?, icon = ?, color = ?, description = ?, is_public = ?
                  WHERE id = ?""",
               (name, request.form.get("icon", "").strip()[:4],
                valid_color(request.form.get("color"), m["server"]["color"]),
                request.form.get("description", "").strip()[:200],
                1 if request.form.get("is_public") else 0, server_id))
    db.commit()
    flash("Server updated. ✨")
    return back(server_id, "overview")


@servers_bp.route("/<int:server_id>/invite/reset", methods=["POST"])
@login_required
def reset_invite(server_id):
    manager_or_404(server_id)
    db = get_db()
    db.execute("UPDATE servers SET invite_code = ? WHERE id = ?",
               (secrets.token_hex(4).upper(), server_id))
    db.commit()
    flash("New invite code created. The old one no longer works.")
    return back(server_id, "invite")


# ----- roles -----

def role_fields(form):
    name = form.get("name", "").strip()
    if not 1 <= len(name) <= 30:
        return None, "Role names are 1-30 characters."
    return (name, valid_color(form.get("color"), "#99aab5"), 1 if form.get("is_admin") else 0), None


@servers_bp.route("/<int:server_id>/roles", methods=["POST"])
@login_required
def add_role(server_id):
    manager_or_404(server_id)
    fields, error = role_fields(request.form)
    db = get_db()
    if error:
        flash(error)
    else:
        top = db.execute("SELECT COALESCE(MAX(position), 0) FROM server_roles WHERE server_id = ?",
                         (server_id,)).fetchone()[0]
        result = db.execute(
            """INSERT OR IGNORE INTO server_roles (server_id, name, color, is_admin, position)
               VALUES (?, ?, ?, ?, ?)""", (server_id, *fields, top + 1))
        db.commit()
        flash(f"Role {fields[0]} created." if result.rowcount else "A role with that name exists.")
    return back(server_id, "roles")


@servers_bp.route("/<int:server_id>/roles/<int:role_id>", methods=["POST"])
@login_required
def edit_role(server_id, role_id):
    manager_or_404(server_id)
    fields, error = role_fields(request.form)
    db = get_db()
    if error:
        flash(error)
    elif db.execute("SELECT 1 FROM server_roles WHERE server_id = ? AND name = ? AND id != ?",
                    (server_id, fields[0], role_id)).fetchone():
        flash("A role with that name exists.")
    else:
        db.execute("UPDATE server_roles SET name = ?, color = ?, is_admin = ? WHERE id = ? AND server_id = ?",
                   (*fields, role_id, server_id))
        db.commit()
        flash("Role saved.")
    return back(server_id, "roles")


@servers_bp.route("/<int:server_id>/roles/<int:role_id>/delete", methods=["POST"])
@login_required
def delete_role(server_id, role_id):
    manager_or_404(server_id)
    db = get_db()
    db.execute("DELETE FROM server_roles WHERE id = ? AND server_id = ?", (role_id, server_id))
    db.commit()
    flash("Role deleted.")
    return back(server_id, "roles")


# ----- categories and channels -----

@servers_bp.route("/<int:server_id>/categories", methods=["POST"])
@login_required
def add_category(server_id):
    manager_or_404(server_id)
    name = request.form.get("name", "").strip()[:30]
    if not name:
        flash("Give the category a name.")
    else:
        db = get_db()
        top = db.execute("SELECT COALESCE(MAX(position), 0) FROM server_categories WHERE server_id = ?",
                         (server_id,)).fetchone()[0]
        db.execute("INSERT INTO server_categories (server_id, name, position) VALUES (?, ?, ?)",
                   (server_id, name, top + 1))
        db.commit()
        flash(f"Category {name} created.")
    return back(server_id, "channels")


@servers_bp.route("/<int:server_id>/categories/<int:category_id>", methods=["POST"])
@login_required
def edit_category(server_id, category_id):
    manager_or_404(server_id)
    name = request.form.get("name", "").strip()[:30]
    if name:
        db = get_db()
        db.execute("UPDATE server_categories SET name = ? WHERE id = ? AND server_id = ?",
                   (name, category_id, server_id))
        db.commit()
        flash("Category renamed.")
    return back(server_id, "channels")


@servers_bp.route("/<int:server_id>/categories/<int:category_id>/delete", methods=["POST"])
@login_required
def delete_category(server_id, category_id):
    manager_or_404(server_id)
    db = get_db()
    db.execute("DELETE FROM server_categories WHERE id = ? AND server_id = ?", (category_id, server_id))
    db.commit()
    flash("Category deleted. Its channels moved to the top.")
    return back(server_id, "channels")


def channel_fields(server_id, form, channel_id=None):
    """Validate a channel form. Returns (values, role_ids, error)."""
    db = get_db()
    name = form.get("name", "").strip().lower().replace(" ", "-").lstrip("#")
    if not CHANNEL_NAME.fullmatch(name):
        return None, None, "Channel names use letters, numbers, - and _ (max 30)."
    if db.execute("SELECT 1 FROM server_channels WHERE server_id = ? AND name = ? AND id IS NOT ?",
                  (server_id, name, channel_id)).fetchone():
        return None, None, f"#{name} already exists."
    category_id = form.get("category_id", type=int)
    if category_id is not None and not db.execute(
            "SELECT 1 FROM server_categories WHERE id = ? AND server_id = ?",
            (category_id, server_id)).fetchone():
        category_id = None
    kind = form.get("kind", "text")
    if kind not in ("text", "announcement"):
        kind = "text"
    valid_roles = {r["id"] for r in db.execute("SELECT id FROM server_roles WHERE server_id = ?",
                                               (server_id,))}
    role_ids = [r for r in form.getlist("role_ids", type=int) if r in valid_roles]
    values = (name, kind, form.get("topic", "").strip()[:120], category_id,
              1 if form.get("is_private") else 0)
    return values, role_ids, None


def set_channel_roles(channel_id, role_ids):
    db = get_db()
    db.execute("DELETE FROM channel_roles WHERE channel_id = ?", (channel_id,))
    db.executemany("INSERT INTO channel_roles (channel_id, role_id) VALUES (?, ?)",
                   [(channel_id, r) for r in role_ids])


@servers_bp.route("/<int:server_id>/channels", methods=["POST"])
@login_required
def add_channel(server_id):
    manager_or_404(server_id)
    values, role_ids, error = channel_fields(server_id, request.form)
    if error:
        flash(error)
        return back(server_id, "channels")
    db = get_db()
    top = db.execute("SELECT COALESCE(MAX(position), 0) FROM server_channels WHERE server_id = ?",
                     (server_id,)).fetchone()[0]
    cid = db.execute(
        """INSERT INTO server_channels (server_id, name, kind, topic, category_id, is_private, position)
           VALUES (?, ?, ?, ?, ?, ?, ?)""", (server_id, *values, top + 1)).lastrowid
    set_channel_roles(cid, role_ids)
    db.commit()
    return redirect(url_for("servers.view", server_id=server_id, channel_id=cid))


@servers_bp.route("/<int:server_id>/channels/<int:channel_id>", methods=["POST"])
@login_required
def edit_channel(server_id, channel_id):
    manager_or_404(server_id)
    db = get_db()
    if not db.execute("SELECT 1 FROM server_channels WHERE id = ? AND server_id = ?",
                      (channel_id, server_id)).fetchone():
        abort(404)
    values, role_ids, error = channel_fields(server_id, request.form, channel_id)
    if error:
        flash(error)
        return back(server_id, "channels")
    db.execute("""UPDATE server_channels SET name = ?, kind = ?, topic = ?, category_id = ?,
                  is_private = ? WHERE id = ?""", (*values, channel_id))
    set_channel_roles(channel_id, role_ids)
    db.commit()
    flash("Channel saved.")
    return back(server_id, "channels")


@servers_bp.route("/<int:server_id>/channels/<int:channel_id>/delete", methods=["POST"])
@login_required
def delete_channel(server_id, channel_id):
    manager_or_404(server_id)
    db = get_db()
    count = db.execute("SELECT COUNT(*) FROM server_channels WHERE server_id = ?",
                       (server_id,)).fetchone()[0]
    if count <= 1:
        flash("A server needs at least one channel.")
    else:
        db.execute("DELETE FROM server_channels WHERE id = ? AND server_id = ?", (channel_id, server_id))
        db.commit()
        flash("Channel deleted.")
    return back(server_id, "channels")


# ----- members -----

@servers_bp.route("/<int:server_id>/members/<int:user_id>/roles", methods=["POST"])
@login_required
def set_member_roles(server_id, user_id):
    manager_or_404(server_id)
    db = get_db()
    if not db.execute("SELECT 1 FROM server_members WHERE server_id = ? AND user_id = ?",
                      (server_id, user_id)).fetchone():
        abort(404)
    valid = {r["id"] for r in db.execute("SELECT id FROM server_roles WHERE server_id = ?", (server_id,))}
    chosen = [r for r in request.form.getlist("role_ids", type=int) if r in valid]
    db.execute("DELETE FROM member_roles WHERE server_id = ? AND user_id = ?", (server_id, user_id))
    db.executemany("INSERT INTO member_roles (server_id, user_id, role_id) VALUES (?, ?, ?)",
                   [(server_id, user_id, r) for r in chosen])
    db.commit()
    flash("Roles updated.")
    return back(server_id, "members")


@servers_bp.route("/<int:server_id>/members/<int:user_id>/kick", methods=["POST"])
@login_required
def kick(server_id, user_id):
    m = manager_or_404(server_id)
    target = membership(server_id, user_id)
    if target is None or target["is_owner"] or user_id == session["user_id"]:
        flash("You can't remove that person.")
    elif target["is_manager"] and not m["is_owner"]:
        flash("Only the owner can remove other admins.")
    else:
        db = get_db()
        db.execute("DELETE FROM server_members WHERE server_id = ? AND user_id = ?", (server_id, user_id))
        db.commit()
        live.notify([user_id], "server", "Removed from a server",
                    f"You were removed from {m['server']['name']}", "/servers/")
        flash("Member removed.")
    return back(server_id, "members")


@servers_bp.route("/channel/<int:channel_id>/attach", methods=["POST"])
@login_required
def attach(channel_id):
    """Send photos, videos, audio, files or a GIF link into a channel."""
    me = session["user_id"]
    m, channel = channel_for(channel_id, me)
    if channel is None or not can_write(m, channel):
        abort(403)
    try:
        items = collect(request)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    db = get_db()
    reply = valid_reply(db, channel_id, request.form.get("reply_to", type=int))
    for path, kind, name in items:
        cur = db.execute(
            """INSERT INTO server_messages (channel_id, sender_id, body, attachment, attach_type, attach_name, reply_to)
               VALUES (?, ?, '', ?, ?, ?, ?)""", (channel_id, me, path, kind, name, reply))
        reply = None
        db.commit()
        message = db.execute(f"{MSG_SELECT} WHERE m.id = :mid",
                             {"mid": cur.lastrowid, "sid": channel["server_id"]}).fetchone()
        socketio.emit("message", dict(message), to=f"srv-{channel_id}")
        live.notify_channel(db, channel, me, live.preview("", kind))
    return jsonify(sent=len(items))
