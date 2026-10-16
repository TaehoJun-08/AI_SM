import json
import random
import re
import secrets
from datetime import datetime
from flask import (Blueprint, render_template, request, g,
                   redirect, url_for, flash, session, abort, jsonify)
from db import get_db
from helpers import login_required
from extensions import socketio
from chatmedia import collect
from servers import perms as P
from servers.messages import MSG_SELECT, valid_reply, recent, post_message, one
import live
import unread
import calls

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
PALETTE = ["#5865f2", "#7c3aed", "#ec4899", "#f59e0b", "#10b981", "#0ea5e9", "#ef4444", "#8b5cf6"]
KINDS = {"text": "# Text", "announcement": "📢 Announcement", "voice": "🔊 Voice room", "forum": "💡 Pitch board"}
TAB_NAMES = {"overview": "Overview", "roles": "Roles & powers", "channels": "Channels", "members": "Members",
             "companions": "Companions", "emoji": "Emoji", "invite": "Invites", "safety": "Safety", "audit": "Audit log"}


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def audit(server_id, action, detail=""):
    """Write one line in the server's audit log (the caller commits)."""
    get_db().execute("INSERT INTO audit_log (server_id, actor_id, action, detail) VALUES (?, ?, ?, ?)",
                     (server_id, session.get("user_id"), action, detail[:300]))


# ---------- permissions ----------

def membership(server_id, user_id):
    """None if the user isn't in the server, else a dict describing what they may do."""
    db = get_db()
    server = db.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
    row = db.execute("SELECT * FROM server_members WHERE server_id = ? AND user_id = ?",
                     (server_id, user_id)).fetchone()
    if server is None or row is None:
        return None
    roles = db.execute(
        """SELECT r.* FROM member_roles m JOIN server_roles r ON r.id = m.role_id
           WHERE m.server_id = ? AND m.user_id = ? ORDER BY r.position DESC, r.id""",
        (server_id, user_id)).fetchall()
    is_owner = server["owner_id"] == user_id
    is_manager = is_owner or any(r["is_admin"] for r in roles)
    if is_manager:
        powers = set(P.ALL)
    else:
        powers = P.to_set(server["base_perms"])
        for r in roles:
            powers |= P.to_set(r["perms"])
    timeout = row["timeout_until"] if row["timeout_until"] and row["timeout_until"] > now_text() else None
    return {"server": server, "is_owner": is_owner, "is_manager": is_manager, "perms": powers,
            "role_ids": {r["id"] for r in roles}, "top": 10**9 if is_owner else
            max([r["position"] for r in roles] + [0]) + (10**6 if is_manager else 0),
            "nickname": row["nickname"], "timeout_until": timeout, "pending_rules": not row["accepted_rules"],
            "can_settings": bool(powers & P.SETTINGS)}


def outranks(m, target):
    """May this member act on that member (kick, ban, change roles)? Owners always can; nobody can on the owner."""
    return not target["is_owner"] and (m["is_owner"] or m["top"] > target["top"])


def can_read(m, channel):
    if channel["kind"] == "thread" and channel["parent_id"]:
        parent = get_db().execute("SELECT * FROM server_channels WHERE id = ?", (channel["parent_id"],)).fetchone()
        return parent is not None and can_read(m, parent)
    if not channel["is_private"] or m["is_manager"]:
        return True
    allowed = {r["role_id"] for r in get_db().execute(
        "SELECT role_id FROM channel_roles WHERE channel_id = ?", (channel["id"],))}
    return bool(allowed & m["role_ids"])


def can_write(m, channel):
    if not can_read(m, channel) or "send" not in m["perms"] or m["timeout_until"] or channel["kind"] == "forum":
        return False
    if (channel["kind"] == "announcement" or channel["read_only"]) and "manage_messages" not in m["perms"]:
        return False
    return True


def channel_for(channel_id, user_id):
    """(membership, channel) if the user may read that channel, otherwise (None, None)."""
    channel = get_db().execute("SELECT * FROM server_channels WHERE id = ?",
                               (channel_id,)).fetchone()
    if channel is None:
        return None, None
    m = membership(channel["server_id"], user_id)
    if m is None or m["pending_rules"] or not can_read(m, channel):
        return None, None
    return m, channel


def need(server_id, *wanted):
    """The caller's membership, or a 404 unless they hold at least one of the wanted powers."""
    m = membership(server_id, session["user_id"])
    if m is None or m["pending_rules"] or (wanted and not (m["perms"] & set(wanted))):
        abort(404)
    return m


def manager_or_404(server_id):
    return need(server_id, "manage_server")


def back(server_id, tab):
    return redirect(url_for("servers.settings", server_id=server_id, tab=tab))


# ---------- shared page data ----------

def my_servers(user_id):
    return get_db().execute(
        """SELECT s.id, s.name, s.icon, s.avatar, s.color FROM servers s
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
    cats = {}
    for pos, title in enumerate(("Information", "Text Channels", "Voice Rooms")):
        cats[title] = db.execute("INSERT INTO server_categories (server_id, name, position) VALUES (?, ?, ?)",
                                 (sid, title, pos)).lastrowid
    starters = [("Information", "announcements", "announcement", "Official news from the owners"),
                ("Text Channels", "general", "text", "Say hi to everyone"),
                ("Text Channels", "pitch-board", "forum", "Share an idea, ask for feedback or funding"),
                ("Voice Rooms", "lounge", "voice", "Hang out, talk or share your screen")]
    for pos, (cat, cname, kind, topic) in enumerate(starters):
        db.execute("""INSERT INTO server_channels (server_id, category_id, name, kind, topic, position)
                      VALUES (?, ?, ?, ?, ?, ?)""", (sid, cats[cat], cname, kind, topic, pos))
    db.execute("INSERT INTO audit_log (server_id, actor_id, action, detail) VALUES (?, ?, 'server.create', ?)",
               (sid, user_id, name))
    db.commit()
    return sid


def valid_color(value, fallback):
    return value if COLOR.fullmatch(value or "") else fallback


def goal_progress(db, server):
    """Money accepted by members' posts, against the server's community goal (None if no goal is set)."""
    if not server["goal_amount"]:
        return None
    raised = db.execute(
        """SELECT COALESCE(SUM(i.amount), 0) FROM interests i JOIN projects p ON p.id = i.project_id
           WHERE i.status = 'accepted'
             AND p.innovator_id IN (SELECT user_id FROM server_members WHERE server_id = ?)""",
        (server["id"],)).fetchone()[0]
    return {"title": server["goal_title"] or "Community goal", "raised": raised, "goal": server["goal_amount"],
            "percent": min(100, round(raised * 100 / server["goal_amount"]))}


# ---------- pages ----------

@servers_bp.route("/")
@login_required
def home():
    me = session["user_id"]
    explore = get_db().execute(
        """SELECT s.*, (SELECT COUNT(*) FROM server_members sm JOIN users hu ON hu.id = sm.user_id AND hu.is_bot = 0 WHERE sm.server_id = s.id) AS members
           FROM servers s WHERE s.is_public = 1
             AND s.id NOT IN (SELECT server_id FROM server_members WHERE user_id = ?)
             AND s.id NOT IN (SELECT server_id FROM server_bans WHERE user_id = ?)
             AND s.project_id IS NULL
           ORDER BY members DESC, s.id DESC LIMIT 24""", (me, me)).fetchall()
    projects = get_db().execute(
        """SELECT s.id, s.color, s.description, p.id AS pid, p.title, p.stage, p.domain, p.funding_needed, p.accepts_money, p.accepts_pilot,
             (SELECT COUNT(*) FROM server_members sm JOIN users hu ON hu.id = sm.user_id AND hu.is_bot = 0 WHERE sm.server_id = s.id) AS members,
             (SELECT COALESCE(SUM(amount), 0) FROM interests WHERE project_id = p.id AND status = 'accepted') AS raised
           FROM servers s JOIN projects p ON p.id = s.project_id
           WHERE s.is_public = 1 AND p.innovator_id != ?
             AND s.id NOT IN (SELECT server_id FROM server_members WHERE user_id = ?)
             AND s.id NOT IN (SELECT server_id FROM server_bans WHERE user_id = ?)
           ORDER BY members DESC, s.id DESC LIMIT 60""", (me, me, me)).fetchall()
    return render_template("servers/home.html", rail=my_servers(me), server=None, explore=explore,
                           projects=projects, domains=sorted({p["domain"] for p in projects}))


@servers_bp.route("/<int:server_id>")
@servers_bp.route("/<int:server_id>/<int:channel_id>")
@login_required
def view(server_id, channel_id=None):
    from servers import companions
    me = session["user_id"]
    m = membership(server_id, me)
    if m is None:
        abort(404)
    if m["pending_rules"]:
        return redirect(url_for("servers.rules_page", server_id=server_id))
    db = get_db()
    server = m["server"]
    categories = db.execute("SELECT * FROM server_categories WHERE server_id = ? ORDER BY position, id",
                            (server_id,)).fetchall()
    readable = [c for c in db.execute(
        "SELECT * FROM server_channels WHERE server_id = ? ORDER BY position, id", (server_id,))
        if can_read(m, c)]
    channels = [c for c in readable if c["kind"] != "thread"]
    groups = [{"category": None, "channels": [c for c in channels if c["category_id"] is None]}]
    groups += [{"category": cat, "channels": [c for c in channels if c["category_id"] == cat["id"]]}
               for cat in categories]
    groups = [g for g in groups if g["channels"] or (g["category"] and "manage_channels" in m["perms"])]
    current = next((c for c in readable if c["id"] == channel_id), None)
    if channel_id is not None and current is None:
        abort(404)
    if current is None and channels:
        current = next(c for g in groups for c in g["channels"])
    if current:
        unread.mark_channel(current["id"], me)
    parent, posts, messages = None, [], []
    if current and current["kind"] == "thread":
        parent = next((c for c in readable if c["id"] == current["parent_id"]), None)
    if current and current["kind"] == "forum":
        posts = db.execute(
            """SELECT t.id, t.name, t.created_by, u.username,
                 (SELECT COUNT(*) FROM server_messages WHERE channel_id = t.id) AS replies,
                 (SELECT MAX(id) FROM server_messages WHERE channel_id = t.id) AS last_id,
                 (SELECT body FROM server_messages WHERE channel_id = t.id ORDER BY id LIMIT 1) AS first_body,
                 (SELECT embed FROM server_messages WHERE channel_id = t.id ORDER BY id LIMIT 1) AS first_embed
               FROM server_channels t LEFT JOIN users u ON u.id = t.created_by
               WHERE t.parent_id = ? AND t.kind = 'thread' ORDER BY last_id DESC""", (current["id"],)).fetchall()
        posts = [dict(p, ask=((json.loads(p["first_embed"]).get("fields") or [{}])[0].get("value")
                              if p["first_embed"] else None)) for p in posts]
    elif current:
        around = request.args.get("msg", type=int)
        messages = recent(db, current, upto=around + 20 if around else None)
    people = db.execute(
        """SELECT u.id, u.username, u.full_name, u.avatar, u.is_bot, sm.nickname,
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
        label = "Companions" if p["is_bot"] else ("Owner" if p["id"] == server["owner_id"] else (p["top_role"] or "Members"))
        member_groups.setdefault(label, []).append(p)
    order = ["Owner"] + [r["name"] for r in db.execute(
        "SELECT name FROM server_roles WHERE server_id = ? ORDER BY position DESC, id", (server_id,))]
    member_groups = [(label, member_groups[label]) for label in order + ["Members", "Companions"]
                     if label in member_groups]
    page = {
        "me": me, "server": server_id, "channel": current["id"] if current else None,
        "members": [{"id": p["id"], "name": p["username"], "bot": bool(p["is_bot"]), "nick": p["nickname"]} for p in people],
        "roles": [dict(name=r["name"], color=r["color"]) for r in db.execute(
            "SELECT name, color FROM server_roles WHERE server_id = ?", (server_id,))],
        "channels": [{"id": c["id"], "name": c["name"]} for c in channels],
        "emojis": {r["name"]: r["path"] for r in db.execute(
            "SELECT name, path FROM server_emojis WHERE server_id = ?", (server_id,))},
        "commands": companions.command_list(db, server_id),
        "can": sorted(m["perms"] & {"manage_messages", "react", "mention_all", "manage_events"}),
        "threadable": bool(current) and current["kind"] in ("text", "announcement", "voice"),
        "slowmode": current["slowmode"] if current and "manage_messages" not in m["perms"] else 0,
    }
    from servers import projectlink
    return render_template("servers/view.html", rail=my_servers(me), server=server, m=m, proj=projectlink.status(db, server),
                           groups=groups, current=current, parent=parent, posts=posts, messages=messages,
                           member_groups=member_groups, member_count=len(people), page=page,
                           goal=goal_progress(db, server),
                           writable=bool(current) and can_write(m, current))


@servers_bp.route("/create", methods=["POST"])
@login_required
def create():
    name = request.form.get("name", "").strip()
    if not 1 <= len(name) <= 40:
        flash("Give your server a name (up to 40 characters).")
        return redirect(url_for("servers.home"))
    sid = create_server(session["user_id"], name, "", random.choice(PALETTE),
                        request.form.get("description", "").strip()[:200],
                        0 if request.form.get("private") else 1)
    flash(f"{name} is ready. Make it yours in Server Settings. ✨")
    return redirect(url_for("servers.view", server_id=sid))


def valid_invite(db, code):
    """(server_id, invite row or None) for a code anyone can type: a limited invite link or the permanent code."""
    row = db.execute("SELECT * FROM server_invites WHERE code = ?", (code,)).fetchone()
    if row and (not row["max_uses"] or row["uses"] < row["max_uses"]) and \
            (not row["expires_at"] or row["expires_at"] > now_text()):
        return row["server_id"], row
    server = db.execute("SELECT id FROM servers WHERE invite_code = ?", (code,)).fetchone()
    return (server["id"], None) if server else (None, None)


@servers_bp.route("/join", methods=["POST"])
@login_required
def join_code():
    code = request.form.get("code", "").strip().upper().split("/")[-1]
    server_id, invite = valid_invite(get_db(), code)
    if server_id is None:
        flash("That invite doesn't exist, has expired, or is used up.")
        return redirect(url_for("servers.home"))
    return redirect(url_for("servers.invite_page", code=code), 303)      # the person confirms there


@servers_bp.route("/<int:server_id>/join", methods=["GET", "POST"])
@login_required
def join_public(server_id):
    """Joining always asks first: GET shows the "Join this server?" page, POST with confirm=1 joins."""
    db = get_db()
    server = db.execute("""SELECT s.*, (SELECT COUNT(*) FROM server_members sm JOIN users hu ON hu.id = sm.user_id AND hu.is_bot = 0 WHERE sm.server_id = s.id) AS members
                           FROM servers s WHERE s.id = ?""", (server_id,)).fetchone()
    if server is None or not server["is_public"]:
        abort(404)
    if membership(server_id, session["user_id"]) is not None:
        return redirect(url_for("servers.view", server_id=server_id))
    if request.method == "POST" and request.form.get("confirm") == "1":
        return join_server(server_id)
    if request.method == "POST":
        return redirect(url_for("servers.join_public", server_id=server_id), 303)
    return render_template("servers/invite.html", rail=my_servers(session["user_id"]), server=server, code=None,
                           joined=False, action=url_for("servers.join_public", server_id=server_id), p=join_preview(db, server))


def join_preview(db, server):
    """A few facts shown on the confirmation page (project servers show the project)."""
    from servers import projectlink
    return projectlink.status(db, server)


def join_server(server_id, invite=None):
    from servers import companions
    db = get_db()
    me = session["user_id"]
    if db.execute("SELECT 1 FROM server_bans WHERE server_id = ? AND user_id = ?", (server_id, me)).fetchone():
        flash("You are banned from that server.")
        return redirect(url_for("servers.home"))
    server = db.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
    joined = db.execute("INSERT OR IGNORE INTO server_members (server_id, user_id, accepted_rules) VALUES (?, ?, ?)",
                        (server_id, me, 0 if server["rules"].strip() else 1)).rowcount
    if joined and invite:
        db.execute("UPDATE server_invites SET uses = uses + 1 WHERE code = ?", (invite["code"],))
    db.commit()
    if joined:
        unread.mark_server_read_from_now(server_id, me)
        live.notify([server["owner_id"]], "server", "New member",
                    f"@{live.username(db, me)} joined {server['name']}", f"/servers/{server_id}")
        from servers import projectlink
        projectlink.on_join(db, server_id, me)
        if not server["rules"].strip():
            companions.on_join(db, server_id, me)
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
    db.execute("DELETE FROM users WHERE id IN (SELECT user_id FROM bots WHERE server_id = ?)", (server_id,))
    db.execute("DELETE FROM servers WHERE id = ?", (server_id,))
    db.commit()
    flash("Server deleted.")
    return redirect(url_for("servers.home"))


# ---------- server settings ----------

def allowed_tabs(m):
    p = m["perms"]
    rules = {"overview": {"manage_server"}, "roles": {"manage_roles"}, "channels": {"manage_channels"},
             "members": {"kick", "ban", "timeout", "manage_roles"}, "companions": {"manage_companions"},
             "emoji": {"manage_server"}, "invite": {"invite", "manage_server"},
             "safety": {"manage_server", "ban", "timeout"}, "audit": {"view_audit"}}
    return [t for t, wanted in rules.items() if p & wanted]


@servers_bp.route("/<int:server_id>/settings")
@login_required
def settings(server_id):
    from servers import companions
    m = need(server_id, *P.SETTINGS, "invite")
    tabs = allowed_tabs(m)
    tab = request.args.get("tab", "")
    if tab not in tabs:
        if not tabs:
            abort(404)
        tab = tabs[0]
    db = get_db()
    ctx = {}
    if tab in ("roles", "channels", "members"):
        ctx["roles"] = db.execute("SELECT * FROM server_roles WHERE server_id = ? ORDER BY position DESC, id",
                                  (server_id,)).fetchall()
    if tab == "roles":
        ctx["role_masks"] = {r["id"]: P.to_set(r["perms"]) for r in ctx["roles"]}
        ctx["base_set"] = P.to_set(m["server"]["base_perms"])
    if tab == "channels":
        ctx["categories"] = db.execute("SELECT * FROM server_categories WHERE server_id = ? ORDER BY position, id",
                                       (server_id,)).fetchall()
        ctx["channels"] = db.execute(
            "SELECT * FROM server_channels WHERE server_id = ? AND kind != 'thread' ORDER BY position, id",
            (server_id,)).fetchall()
        channel_roles = {}
        for row in db.execute("""SELECT cr.channel_id, cr.role_id FROM channel_roles cr
                                 JOIN server_channels c ON c.id = cr.channel_id WHERE c.server_id = ?""",
                              (server_id,)):
            channel_roles.setdefault(row["channel_id"], set()).add(row["role_id"])
        ctx["channel_roles"] = channel_roles
    if tab == "members":
        ctx["members"] = db.execute(
            """SELECT u.id, u.username, u.full_name, u.avatar, u.is_bot, sm.nickname, sm.timeout_until
               FROM server_members sm JOIN users u ON u.id = sm.user_id WHERE sm.server_id = ?
               ORDER BY u.username COLLATE NOCASE""", (server_id,)).fetchall()
        member_roles = {}
        for row in db.execute("SELECT user_id, role_id FROM member_roles WHERE server_id = ?", (server_id,)):
            member_roles.setdefault(row["user_id"], set()).add(row["role_id"])
        ctx["member_roles"] = member_roles
        ctx["tops"] = {p["id"]: membership(server_id, p["id"]) for p in ctx["members"]}
    if tab == "companions":
        ctx.update(companions.settings_data(db, server_id))
        ctx["new_token"] = session.pop("new_token", None)
    if tab == "emoji":
        ctx["emojis"] = db.execute("SELECT * FROM server_emojis WHERE server_id = ? ORDER BY name", (server_id,)).fetchall()
    if tab == "invite":
        ctx["invites"] = db.execute("SELECT * FROM server_invites WHERE server_id = ? ORDER BY created_at DESC",
                                    (server_id,)).fetchall()
    if tab == "safety":
        ctx["bans"] = db.execute(
            """SELECT b.*, u.username FROM server_bans b JOIN users u ON u.id = b.user_id
               WHERE b.server_id = ? ORDER BY b.created_at DESC""", (server_id,)).fetchall()
    if tab == "audit":
        ctx["entries"] = db.execute(
            """SELECT a.*, u.username FROM audit_log a LEFT JOIN users u ON u.id = a.actor_id
               WHERE a.server_id = ? ORDER BY a.id DESC LIMIT 150""", (server_id,)).fetchall()
    return render_template("servers/settings.html", rail=my_servers(session["user_id"]),
                           server=m["server"], m=m, tab=tab, tabs=tabs, tab_names=TAB_NAMES,
                           perm_list=P.PERMS, kinds=KINDS, **ctx)


@servers_bp.route("/<int:server_id>/settings/overview", methods=["POST"])
@login_required
def save_overview(server_id):
    m = need(server_id, "manage_server")
    name = request.form.get("name", "").strip()
    if not 1 <= len(name) <= 40:
        flash("Server names are 1-40 characters.")
        return back(server_id, "overview")
    db = get_db()
    db.execute("""UPDATE servers SET name = ?, description = ?, is_public = ?,
                  goal_title = ?, goal_amount = ? WHERE id = ?""",
               (name, request.form.get("description", "").strip()[:200],
                1 if request.form.get("is_public") else 0,
                request.form.get("goal_title", "").strip()[:60],
                max(0, request.form.get("goal_amount", 0, type=int) or 0), server_id))
    audit(server_id, "server.update", name)
    db.commit()
    flash("Server updated. ✨")
    return back(server_id, "overview")


@servers_bp.route("/<int:server_id>/photo", methods=["POST"])
@login_required
def save_server_photo(server_id):
    from media import save_avatar, delete_media
    m = need(server_id, "manage_server")
    db = get_db()
    if request.form.get("remove"):
        old = m["server"]["avatar"]
        db.execute("UPDATE servers SET avatar = NULL WHERE id = ?", (server_id,))
        audit(server_id, "server.photo_removed")
        db.commit()
        delete_media(old)
        flash("Server photo removed.")
        return back(server_id, "overview")
    file = request.files.get("photo")
    if file is None or not file.filename:
        flash("Choose a picture first.")
        return back(server_id, "overview")
    try:
        path = save_avatar(file)
    except ValueError as e:
        flash(str(e))
        return back(server_id, "overview")
    old = m["server"]["avatar"]
    db.execute("UPDATE servers SET avatar = ? WHERE id = ?", (path, server_id))
    audit(server_id, "server.photo_updated")
    db.commit()
    delete_media(old)
    flash("Looking good! Server photo updated. 📸")
    return back(server_id, "overview")


@servers_bp.route("/<int:server_id>/invite/reset", methods=["POST"])
@login_required
def reset_invite(server_id):
    need(server_id, "manage_server")
    db = get_db()
    db.execute("UPDATE servers SET invite_code = ? WHERE id = ?",
               (secrets.token_hex(4).upper(), server_id))
    audit(server_id, "invite.reset")
    db.commit()
    flash("New invite code created. The old one no longer works.")
    return back(server_id, "invite")


# ----- roles -----

def role_fields(form, m):
    name = form.get("name", "").strip()
    if not 1 <= len(name) <= 30:
        return None, "Role names are 1-30 characters."
    granted = [k for k in form.getlist("perms") if k in P.BIT and k in m["perms"]]
    admin = 1 if form.get("is_admin") and m["is_manager"] else 0
    return (name, valid_color(form.get("color"), "#99aab5"), admin, P.to_mask(granted)), None


@servers_bp.route("/<int:server_id>/roles", methods=["POST"])
@login_required
def add_role(server_id):
    m = need(server_id, "manage_roles")
    fields, error = role_fields(request.form, m)
    db = get_db()
    if error:
        flash(error)
    else:
        top = db.execute("SELECT COALESCE(MAX(position), 0) FROM server_roles WHERE server_id = ?",
                         (server_id,)).fetchone()[0]
        position = top + 1 if m["is_manager"] else max(1, min(top + 1, m["top"] - 1))
        result = db.execute(
            """INSERT OR IGNORE INTO server_roles (server_id, name, color, is_admin, perms, position)
               VALUES (?, ?, ?, ?, ?, ?)""", (server_id, *fields, position))
        audit(server_id, "role.create", fields[0])
        db.commit()
        flash(f"Role {fields[0]} created." if result.rowcount else "A role with that name exists.")
    return back(server_id, "roles")


@servers_bp.route("/<int:server_id>/roles/<int:role_id>", methods=["POST"])
@login_required
def edit_role(server_id, role_id):
    m = need(server_id, "manage_roles")
    db = get_db()
    role = db.execute("SELECT * FROM server_roles WHERE id = ? AND server_id = ?", (role_id, server_id)).fetchone()
    if role is None:
        abort(404)
    fields, error = role_fields(request.form, m)
    if not m["is_manager"] and role["position"] >= m["top"]:
        flash("You can only edit roles below your own.")
    elif error:
        flash(error)
    elif db.execute("SELECT 1 FROM server_roles WHERE server_id = ? AND name = ? AND id != ?",
                    (server_id, fields[0], role_id)).fetchone():
        flash("A role with that name exists.")
    else:
        keep = role["perms"] & ~P.to_mask(m["perms"])      # powers you don't hold stay as they were
        admin = fields[2] if m["is_manager"] else role["is_admin"]
        db.execute("UPDATE server_roles SET name = ?, color = ?, is_admin = ?, perms = ? WHERE id = ? AND server_id = ?",
                   (fields[0], fields[1], admin, fields[3] | keep, role_id, server_id))
        audit(server_id, "role.update", fields[0])
        db.commit()
        flash("Role saved.")
    return back(server_id, "roles")


@servers_bp.route("/<int:server_id>/roles/<int:role_id>/delete", methods=["POST"])
@login_required
def delete_role(server_id, role_id):
    m = need(server_id, "manage_roles")
    db = get_db()
    role = db.execute("SELECT * FROM server_roles WHERE id = ? AND server_id = ?", (role_id, server_id)).fetchone()
    if role and (m["is_manager"] or role["position"] < m["top"]):
        db.execute("DELETE FROM server_roles WHERE id = ?", (role_id,))
        audit(server_id, "role.delete", role["name"])
        db.commit()
        flash("Role deleted.")
    return back(server_id, "roles")


@servers_bp.route("/<int:server_id>/base-powers", methods=["POST"])
@login_required
def save_base_powers(server_id):
    m = need(server_id, "manage_roles")
    granted = [k for k in request.form.getlist("perms") if k in P.BIT and k in m["perms"]]
    keep = m["server"]["base_perms"] & ~P.to_mask(m["perms"])
    db = get_db()
    db.execute("UPDATE servers SET base_perms = ? WHERE id = ?", (P.to_mask(granted) | keep, server_id))
    audit(server_id, "role.everyone", ", ".join(granted))
    db.commit()
    flash("Powers for everyone saved.")
    return back(server_id, "roles")


# ----- categories and channels -----

@servers_bp.route("/<int:server_id>/categories", methods=["POST"])
@login_required
def add_category(server_id):
    need(server_id, "manage_channels")
    name = request.form.get("name", "").strip()[:30]
    if not name:
        flash("Give the category a name.")
    else:
        db = get_db()
        top = db.execute("SELECT COALESCE(MAX(position), 0) FROM server_categories WHERE server_id = ?",
                         (server_id,)).fetchone()[0]
        db.execute("INSERT INTO server_categories (server_id, name, position) VALUES (?, ?, ?)",
                   (server_id, name, top + 1))
        audit(server_id, "category.create", name)
        db.commit()
        flash(f"Category {name} created.")
    return back(server_id, "channels")


@servers_bp.route("/<int:server_id>/categories/<int:category_id>", methods=["POST"])
@login_required
def edit_category(server_id, category_id):
    need(server_id, "manage_channels")
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
    need(server_id, "manage_channels")
    db = get_db()
    db.execute("DELETE FROM server_categories WHERE id = ? AND server_id = ?", (category_id, server_id))
    audit(server_id, "category.delete", str(category_id))
    db.commit()
    flash("Category deleted. Its channels moved to the top.")
    return back(server_id, "channels")


def channel_fields(server_id, form, channel_id=None):
    """Validate a channel form. Returns (values, role_ids, error)."""
    db = get_db()
    name = form.get("name", "").strip().lower().replace(" ", "-").lstrip("#")
    if not CHANNEL_NAME.fullmatch(name):
        return None, None, "Channel names use letters, numbers, - and _ (max 30)."
    if db.execute("SELECT 1 FROM server_channels WHERE server_id = ? AND name = ? AND kind != 'thread' AND id IS NOT ?",
                  (server_id, name, channel_id)).fetchone():
        return None, None, f"#{name} already exists."
    category_id = form.get("category_id", type=int)
    if category_id is not None and not db.execute(
            "SELECT 1 FROM server_categories WHERE id = ? AND server_id = ?",
            (category_id, server_id)).fetchone():
        category_id = None
    kind = form.get("kind", "text")
    if kind not in KINDS:
        kind = "text"
    valid_roles = {r["id"] for r in db.execute("SELECT id FROM server_roles WHERE server_id = ?",
                                               (server_id,))}
    role_ids = [r for r in form.getlist("role_ids", type=int) if r in valid_roles]
    slow = min(max(form.get("slowmode", 0, type=int) or 0, 0), 21600)
    values = (name, kind, form.get("topic", "").strip()[:120], category_id,
              1 if form.get("is_private") else 0, slow, 1 if form.get("read_only") else 0)
    return values, role_ids, None


def set_channel_roles(channel_id, role_ids):
    db = get_db()
    db.execute("DELETE FROM channel_roles WHERE channel_id = ?", (channel_id,))
    db.executemany("INSERT INTO channel_roles (channel_id, role_id) VALUES (?, ?)",
                   [(channel_id, r) for r in role_ids])


@servers_bp.route("/<int:server_id>/channels", methods=["POST"])
@login_required
def add_channel(server_id):
    need(server_id, "manage_channels")
    values, role_ids, error = channel_fields(server_id, request.form)
    if error:
        flash(error)
        return back(server_id, "channels")
    db = get_db()
    top = db.execute("SELECT COALESCE(MAX(position), 0) FROM server_channels WHERE server_id = ?",
                     (server_id,)).fetchone()[0]
    cid = db.execute(
        """INSERT INTO server_channels (server_id, name, kind, topic, category_id, is_private, slowmode, read_only, position)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (server_id, *values, top + 1)).lastrowid
    set_channel_roles(cid, role_ids)
    audit(server_id, "channel.create", f"#{values[0]}")
    db.commit()
    return redirect(url_for("servers.view", server_id=server_id, channel_id=cid))


@servers_bp.route("/<int:server_id>/channels/<int:channel_id>", methods=["POST"])
@login_required
def edit_channel(server_id, channel_id):
    need(server_id, "manage_channels")
    db = get_db()
    if not db.execute("SELECT 1 FROM server_channels WHERE id = ? AND server_id = ?",
                      (channel_id, server_id)).fetchone():
        abort(404)
    values, role_ids, error = channel_fields(server_id, request.form, channel_id)
    if error:
        flash(error)
        return back(server_id, "channels")
    db.execute("""UPDATE server_channels SET name = ?, kind = ?, topic = ?, category_id = ?,
                  is_private = ?, slowmode = ?, read_only = ? WHERE id = ?""", (*values, channel_id))
    set_channel_roles(channel_id, role_ids)
    audit(server_id, "channel.update", f"#{values[0]}")
    db.commit()
    flash("Channel saved.")
    return back(server_id, "channels")


@servers_bp.route("/<int:server_id>/channels/<int:channel_id>/delete", methods=["POST"])
@login_required
def delete_channel(server_id, channel_id):
    need(server_id, "manage_channels")
    db = get_db()
    count = db.execute("SELECT COUNT(*) FROM server_channels WHERE server_id = ? AND kind != 'thread'",
                       (server_id,)).fetchone()[0]
    row = db.execute("SELECT name FROM server_channels WHERE id = ? AND server_id = ?", (channel_id, server_id)).fetchone()
    if count <= 1:
        flash("A server needs at least one channel.")
    elif row:
        db.execute("DELETE FROM server_channels WHERE id = ? AND server_id = ?", (channel_id, server_id))
        audit(server_id, "channel.delete", f"#{row['name']}")
        db.commit()
        flash("Channel deleted.")
    return back(server_id, "channels")


# ----- members -----

@servers_bp.route("/<int:server_id>/members/<int:user_id>/roles", methods=["POST"])
@login_required
def set_member_roles(server_id, user_id):
    m = need(server_id, "manage_roles")
    db = get_db()
    target = membership(server_id, user_id)
    if target is None:
        abort(404)
    if not outranks(m, target):
        flash("You can only change roles of people ranked below you.")
        return back(server_id, "members")
    rows = db.execute("SELECT id, position FROM server_roles WHERE server_id = ?", (server_id,)).fetchall()
    allowed = {r["id"] for r in rows if m["is_manager"] or r["position"] < m["top"]}
    chosen = {r for r in request.form.getlist("role_ids", type=int) if r in allowed}
    keep = {r for r in target["role_ids"] if r not in allowed}       # roles above you stay untouched
    db.execute("DELETE FROM member_roles WHERE server_id = ? AND user_id = ?", (server_id, user_id))
    db.executemany("INSERT INTO member_roles (server_id, user_id, role_id) VALUES (?, ?, ?)",
                   [(server_id, user_id, r) for r in chosen | keep])
    audit(server_id, "member.roles", live.username(db, user_id))
    db.commit()
    flash("Roles updated.")
    return back(server_id, "members")


@servers_bp.route("/channel/<int:channel_id>/attach", methods=["POST"])
@login_required
def attach(channel_id):
    """Send photos, videos, audio, files or a GIF link into a channel."""
    me = session["user_id"]
    m, channel = channel_for(channel_id, me)
    if channel is None or not can_write(m, channel) or "attach" not in m["perms"]:
        abort(403)
    try:
        items = collect(request)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    db = get_db()
    reply = valid_reply(db, channel_id, request.form.get("reply_to", type=int))
    for path, kind, name in items:
        post_message(db, channel, me, "", reply_to=reply, attachment=path, attach_type=kind, attach_name=name)
        reply = None
    return jsonify(sent=len(items))


from servers import mod, extras, projectlink   # noqa: E402,F401  (more routes on the same blueprint)
