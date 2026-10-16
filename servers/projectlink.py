"""One project, one server. The owner opens a server from the project's post; everybody who wants
to follow that project joins it, and everything in there is about that project only: updates,
questions, sponsors and a live status card (stage, funding, sponsors)."""
import json
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash, session, abort, request
from media import save_media, delete_media
from db import get_db
from helpers import login_required
import live
from servers import companions
from servers.messages import post_message
from servers.routes import servers_bp, membership, create_server, join_server

STAGES = ["idea", "prototype", "mvp", "growth"]
STAGE_LABEL = {"idea": "💡 Idea", "prototype": "🧪 Prototype", "mvp": "🚀 MVP", "growth": "📈 Growth"}
STAGE_COLOR = {"idea": "#f59e0b", "prototype": "#0ea5e9", "mvp": "#8b5cf6", "growth": "#10b981"}


def server_of(project_id):
    """The id of the server linked to a post, or None (also used by templates)."""
    row = get_db().execute("SELECT id FROM servers WHERE project_id = ?", (project_id,)).fetchone()
    return row["id"] if row else None


def raised(db, project_id):
    return db.execute("SELECT COALESCE(SUM(amount), 0) FROM interests WHERE project_id = ? AND status = 'accepted'",
                      (project_id,)).fetchone()[0]


def status(db, server):
    """What the status card in a project server shows (None for ordinary servers)."""
    if not server["project_id"]:
        return None
    p = db.execute("""SELECT p.*, u.username FROM projects p JOIN users u ON u.id = p.innovator_id
                      WHERE p.id = ?""", (server["project_id"],)).fetchone()
    if p is None:
        return None
    got = raised(db, p["id"])
    sponsors = db.execute("SELECT COUNT(*) FROM interests WHERE project_id = ? AND status = 'accepted'", (p["id"],)).fetchone()[0]
    free = db.execute("SELECT COUNT(*) FROM interests WHERE project_id = ? AND status = 'accepted' AND pilot = 1", (p["id"],)).fetchone()[0]
    import deals
    wants = " · ".join(deals.LABEL[k] for k in deals.kinds_of(p))
    return {"id": p["id"], "title": p["title"], "owner": p["username"], "stage": p["stage"], "stage_i": STAGES.index(p["stage"]),
            "steps": [STAGE_LABEL[s] for s in STAGES], "ask": p["funding_needed"], "raised": got, "sponsors": sponsors,
            "money": bool(p["accepts_money"]), "free": free, "wants": wants,
            "percent": min(100, round(got * 100 / p["funding_needed"])) if p["funding_needed"] and p["accepts_money"] else 0,
            "milestones": milestones(db, p["id"], got)}


def status_embed(db, server):
    s = status(db, server)
    if s is None:
        return None
    steps = " → ".join(f"**{label}**" if i == s["stage_i"] else label for i, label in enumerate(s["steps"]))
    return {"kind": "project", "title": f"Status: {s['title']}", "url": f"/p/{s['id']}", "color": STAGE_COLOR[s["stage"]],
            "description": steps,
            "fields": [{"name": "Owner", "value": f"@{s['owner']}", "inline": True},
                       {"name": "Sponsors", "value": str(s["sponsors"]) + (f" ({s['free']} free)" if s["free"] else ""), "inline": True},
                       {"name": "Asking" if s["money"] else "Wants", "value": f"₹{s['ask']:,}" if s["money"] else s["wants"], "inline": True}]
                      + ([{"name": "Milestones", "value": " · ".join(("✅ " if m["state"] != "open" else "⬜ ") + m["title"] for m in s["milestones"])}]
                         if s["milestones"] else []),
            **({"progress": {"value": s["raised"], "goal": s["ask"], "label": f"₹{s['raised']:,} of ₹{s['ask']:,} sponsored"}} if s["money"] else {}),
            "footer": "Open the project post to sponsor or comment"}


def scout_post(db, server_id, text="", embed=None):
    """Say something in the project server, as Sponsor Scout, in the channel Scout was given
    (#announcements by default). Returns True if it was really posted."""
    bots = companions.bots_of(db, server_id, "scout")
    if not bots:
        return False
    channel = companions.channel_from_config(db, server_id, bots[0]["cfg"], "announcements")
    return bool(channel and post_message(db, channel, bots[0]["user_id"], text, embed=embed))


def _backlog(db, project_id):
    return db.execute("SELECT COUNT(*) FROM pending_posts WHERE project_id = ?", (project_id,)).fetchone()[0]


def project_post(db, project_id, text="", embed=None):
    """A message that belongs to ONE project: it goes to that project's server only. If the server (or
    Scout's channel) isn't ready, it waits in the queue and is sent later by flush_pending()."""
    sid = server_of(project_id)
    waiting = _backlog(db, project_id)
    if sid and not waiting and scout_post(db, sid, text, embed):
        return True
    db.execute("INSERT INTO pending_posts (project_id, body, embed) VALUES (?, ?, ?)",
               (project_id, text, json.dumps(embed) if embed else None))
    db.commit()
    if not waiting:                      # tell the owner once, when the queue starts
        project = db.execute("SELECT title, innovator_id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if project:
            live.notify([project["innovator_id"]], "server", f"Set up a server for “{project['title']}”",
                        "Messages about this project are waiting. They'll be sent as soon as the server is ready.",
                        f"/servers/project/{project_id}/setup" if sid is None else f"/servers/{sid}")
    return False


def flush_pending(db, project_id=None):
    """Send queued messages, oldest first, for one project (or all). Stops at the first one that can't be
    delivered yet so the order is kept."""
    ids = [project_id] if project_id else [r[0] for r in db.execute("SELECT DISTINCT project_id FROM pending_posts")]
    for pid in ids:
        sid = server_of(pid)
        if sid is None:
            continue
        for row in db.execute("SELECT * FROM pending_posts WHERE project_id = ? ORDER BY id", (pid,)).fetchall():
            if not scout_post(db, sid, row["body"], json.loads(row["embed"]) if row["embed"] else None):
                break
            db.execute("DELETE FROM pending_posts WHERE id = ?", (row["id"],))
            db.commit()


def on_stage_change(db, project_id, old, new, old_ask, new_ask):
    sid = server_of(project_id)
    if sid is None or (old == new and old_ask == new_ask):
        return
    server = db.execute("SELECT * FROM servers WHERE id = ?", (sid,)).fetchone()
    if old != new:
        db.execute("UPDATE servers SET color = ? WHERE id = ?", (STAGE_COLOR[new], sid))
        db.commit()
    embed = status_embed(db, server)
    embed["title"] = f"{STAGE_LABEL[old]} → {STAGE_LABEL[new]}" if old != new else f"Funding goal is now ₹{new_ask:,}"
    scout_post(db, sid, embed=embed)
    notify_followers(db, project_id, f"{server['name']}: {embed['title']}", "Open the project to see the new status")
    live.server_changed(sid)


def sync_sponsors(db, server_id):
    """Everyone whose offer was accepted and who is in the server gets the Sponsor role."""
    server = db.execute("SELECT project_id FROM servers WHERE id = ?", (server_id,)).fetchone()
    role = db.execute("SELECT id FROM server_roles WHERE server_id = ? AND name = 'Sponsor'", (server_id,)).fetchone()
    if not server or not server["project_id"] or not role:
        return
    for r in db.execute("""SELECT i.sponsor_id FROM interests i JOIN server_members m
                           ON m.user_id = i.sponsor_id AND m.server_id = ?
                           WHERE i.project_id = ? AND i.status = 'accepted'""", (server_id, server["project_id"])):
        db.execute("INSERT OR IGNORE INTO member_roles (server_id, user_id, role_id) VALUES (?, ?, ?)",
                   (server_id, r["sponsor_id"], role["id"]))
    db.commit()


def on_sponsorship(db, project_id, sponsor_id):
    """An offer was accepted: sponsor gets the role if already inside, otherwise an invitation."""
    follow(db, project_id, sponsor_id)
    check_milestones(db, project_id)
    sid = server_of(project_id)
    if sid is None:
        return
    sync_sponsors(db, sid)
    if membership(sid, sponsor_id) is None:
        title = db.execute("SELECT title FROM projects WHERE id = ?", (project_id,)).fetchone()["title"]
        live.notify([sponsor_id], "server", "Follow your project",
                    f"“{title}” has its own server with updates and a sponsors room", f"/p/{project_id}")
    live.server_changed(sid)


def create_project_server(db, project, name=None):
    owner = project["innovator_id"]
    sid = create_server(owner, (name or project["title"])[:40], "💡", STAGE_COLOR[project["stage"]], project["summary"][:200], 1)
    db.execute("UPDATE servers SET project_id = ? WHERE id = ?", (project["id"], sid))
    db.execute("UPDATE server_channels SET topic = 'Project updates from the team' WHERE server_id = ? AND name = 'announcements'", (sid,))
    db.execute("UPDATE server_channels SET name = 'feedback', topic = 'Suggestions and ideas for this project' WHERE server_id = ? AND kind = 'forum'", (sid,))
    db.execute("UPDATE server_channels SET name = 'lounge', topic = 'Talk with the team' WHERE server_id = ? AND kind = 'voice'", (sid,))
    text_cat = db.execute("SELECT id FROM server_categories WHERE server_id = ? AND name = 'Text Channels'", (sid,)).fetchone()["id"]
    roles = {}
    for pos, (name, color) in enumerate((("Sponsor", "#f59e0b"), ("Team", "#38bdf8")), start=1):
        roles[name] = db.execute("INSERT INTO server_roles (server_id, name, color, position) VALUES (?, ?, ?, ?)",
                                 (sid, name, color, pos)).lastrowid
    extra = [("q-and-a", "Ask anything about the project", 0, None), ("sponsors", "Only sponsors and the team", 1, "Sponsor"),
             ("team", "Team only", 1, "Team")]
    for pos, (name, topic, private, role) in enumerate(extra, start=10):
        cid = db.execute("""INSERT INTO server_channels (server_id, category_id, name, topic, is_private, position)
                            VALUES (?, ?, ?, ?, ?, ?)""", (sid, text_cat, name, topic, private, pos)).lastrowid
        if role:
            db.execute("INSERT INTO channel_roles (channel_id, role_id) VALUES (?, ?)", (cid, roles[role]))
        if name == "sponsors":
            db.execute("INSERT OR IGNORE INTO channel_roles (channel_id, role_id) VALUES (?, ?)", (cid, roles["Team"]))
    db.execute("INSERT OR IGNORE INTO member_roles (server_id, user_id, role_id) VALUES (?, ?, ?)", (sid, owner, roles["Team"]))
    ann = db.execute("SELECT id FROM server_channels WHERE server_id = ? AND name = 'announcements'", (sid,)).fetchone()["id"]
    db.commit()
    scout, _ = companions.install(db, sid, "scout", owner)
    db.execute("UPDATE bots SET config = ? WHERE id = ?", ('{"channel": "%d"}' % ann, scout))
    companions.install(db, sid, "herald", owner)
    db.commit()
    scout_post(db, sid, embed=status_embed(db, db.execute("SELECT * FROM servers WHERE id = ?", (sid,)).fetchone()))
    for r in db.execute("SELECT sponsor_id FROM interests WHERE project_id = ? AND status = 'accepted'", (project["id"],)):
        live.notify([r["sponsor_id"]], "server", "Project server opened", f"“{project['title']}” now has its own server", f"/servers/{sid}")
    flush_pending(db, project["id"])      # anything that happened before the server existed
    return sid


@servers_bp.route("/project/<int:project_id>/setup", methods=["GET", "POST"])
@login_required
def setup_project(project_id):
    """Shown right after posting a project: name the project's server and open it."""
    db = get_db()
    project = owned(db, project_id)
    sid = server_of(project_id)
    if sid:
        return redirect(url_for("servers.view", server_id=sid))
    if request.method == "POST":
        name = " ".join(request.form.get("name", "").split())[:40] or project["title"][:40]
        sid = create_project_server(db, project, name)
        flash("Your project server is ready. Share the post so people can follow it. ✨")
        return redirect(url_for("servers.view", server_id=sid))
    return render_template("servers/project_setup.html", project=project, waiting=_backlog(db, project_id),
                           color=STAGE_COLOR[project["stage"]])


@servers_bp.route("/project/<int:project_id>/open", methods=["POST"])
@login_required
def open_project(project_id):
    """Owner: create the project's server (once). Anyone else: follow it (join)."""
    db = get_db()
    me = session["user_id"]
    project = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if project is None:
        abort(404)
    sid = server_of(project_id)
    if sid is None:
        if project["innovator_id"] != me:
            flash("The owner hasn't opened a server for this project yet.")
            return redirect(url_for("feed.post", post_id=project_id))
        sid = create_project_server(db, project)
        flash("Your project server is ready. Share the post so people can follow it. ✨")
        return redirect(url_for("servers.view", server_id=sid))
    if membership(sid, me) is not None:
        return redirect(url_for("servers.view", server_id=sid))
    return redirect(url_for("servers.join_public", server_id=sid), 303)      # ask before joining


# ---------- followers, updates, milestones ----------

proj_bp = Blueprint("projupdates", __name__, url_prefix="/p")
MAX_MILESTONES = 6


def follow(db, project_id, user_id):
    db.execute("INSERT OR IGNORE INTO project_follows (project_id, user_id) VALUES (?, ?)", (project_id, user_id))
    db.commit()


def follower_ids(db, project_id):
    """Followers plus everyone whose offer was accepted: sponsors follow automatically."""
    return [r["user_id"] for r in db.execute(
        """SELECT user_id FROM project_follows WHERE project_id = ?
           UNION SELECT sponsor_id FROM interests WHERE project_id = ? AND status = 'accepted'""", (project_id, project_id))]


def notify_followers(db, project_id, title, body, skip=()):
    ids = [u for u in follower_ids(db, project_id) if u not in skip]
    if ids:
        live.notify(ids, "server", title, body, f"/p/{project_id}")


def on_join(db, server_id, user_id):
    """Someone joined a project server: they follow the project and (if sponsoring) get the Sponsor role."""
    server = db.execute("SELECT project_id, owner_id FROM servers WHERE id = ?", (server_id,)).fetchone()
    if server and server["project_id"]:
        if user_id != server["owner_id"]:
            follow(db, server["project_id"], user_id)
        sync_sponsors(db, server_id)


def ago(stamp):
    if not stamp:
        return None
    seconds = max(int((datetime.now() - datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")).total_seconds()), 0)
    for size, word in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            n = seconds // size
            return f"{n} {word}{'s' if n != 1 else ''} ago"
    return "just now"


def chip(project_id):
    """The small "12 members · last update 2 days ago" line shown on posts and feed cards."""
    db = get_db()
    row = db.execute(
        """SELECT s.id, p.stage, (SELECT COUNT(*) FROM server_members sm JOIN users hu ON hu.id = sm.user_id AND hu.is_bot = 0 WHERE sm.server_id = s.id) AS members,
             (SELECT MAX(created_at) FROM project_updates WHERE project_id = p.id) AS last_update
           FROM servers s JOIN projects p ON p.id = s.project_id WHERE s.project_id = ?""", (project_id,)).fetchone()
    if row is None:
        return None
    return {"server_id": row["id"], "members": row["members"], "stage": STAGE_LABEL[row["stage"]], "last": ago(row["last_update"])}


def milestones(db, project_id, got=None):
    got = raised(db, project_id) if got is None else got
    items = []
    for m in db.execute("SELECT * FROM project_milestones WHERE project_id = ? ORDER BY amount, id", (project_id,)):
        state = "done" if m["done"] else ("funded" if got >= m["amount"] else "open")
        items.append(dict(m, state=state))
    return items


def post_extras(db, project_id, me):
    return {"milestones": milestones(db, project_id),
            "updates": db.execute("""SELECT up.*, u.username FROM project_updates up JOIN users u ON u.id = up.author_id
                                     WHERE up.project_id = ? ORDER BY up.id DESC LIMIT 10""", (project_id,)).fetchall(),
            "followers": len(follower_ids(db, project_id)),
            "following": me in follower_ids(db, project_id)}


def check_milestones(db, project_id):
    """Announce each milestone once, the moment accepted sponsorships cover its amount."""
    got = raised(db, project_id)
    title = db.execute("SELECT title, innovator_id FROM projects WHERE id = ?", (project_id,)).fetchone()
    sid = server_of(project_id)
    for m in db.execute("SELECT * FROM project_milestones WHERE project_id = ? AND announced = 0 AND amount <= ?", (project_id, got)).fetchall():
        db.execute("UPDATE project_milestones SET announced = 1 WHERE id = ?", (m["id"],))
        db.commit()
        project_post(db, project_id, embed={"title": f"🎯 Milestone reached: {m['title']}", "color": "#10b981",
                                            "description": f"₹{m['amount']:,} is covered. Thank you, sponsors!",
                                            "progress": {"value": got, "goal": max(got, m["amount"]), "label": f"₹{got:,} sponsored"}})
        notify_followers(db, project_id, f"🎯 {title['title']}: milestone reached", m["title"])
    live.touch(["posts"])


def owned(db, project_id):
    project = db.execute("SELECT * FROM projects WHERE id = ? AND innovator_id = ?", (project_id, session["user_id"])).fetchone()
    if project is None:
        abort(404)
    return project


def back_to_post(project_id):
    return redirect(url_for("feed.post", post_id=project_id))


@proj_bp.route("/<int:project_id>/update", methods=["POST"])
@login_required
def add_update(project_id):
    db = get_db()
    project = owned(db, project_id)
    body = request.form.get("body", "").strip()[:500]
    if not body:
        flash("Write what changed.")
        return back_to_post(project_id)
    image = None
    try:
        path, kind = save_media(request.files.get("image"))
        image = path if kind == "image" else None
        if kind == "video":
            delete_media(path)
            flash("Updates take a photo, not a video.")
    except ValueError as e:
        flash(str(e))
    db.execute("INSERT INTO project_updates (project_id, author_id, body, image) VALUES (?, ?, ?, ?)",
               (project_id, session["user_id"], body, image))
    db.commit()
    embed = {"title": "📣 Project update", "description": body, "color": "#f59e0b", "footer": f"by @{live.username(db, session['user_id'])}"}
    if image:
        embed["image"] = "/static/" + image
    project_post(db, project_id, embed=embed)
    notify_followers(db, project_id, f"📣 {project['title']}", body)
    live.to_feed("post_changed", {"id": project_id})
    live.touch(["posts"])
    flash("Update posted. Followers were notified.")
    return back_to_post(project_id)


@proj_bp.route("/<int:project_id>/update/<int:update_id>/delete", methods=["POST"])
@login_required
def delete_update(project_id, update_id):
    db = get_db()
    owned(db, project_id)
    row = db.execute("SELECT image FROM project_updates WHERE id = ? AND project_id = ?", (update_id, project_id)).fetchone()
    if row:
        db.execute("DELETE FROM project_updates WHERE id = ?", (update_id,))
        db.commit()
        delete_media(row["image"])
        live.touch(["posts"])
    return back_to_post(project_id)


@proj_bp.route("/<int:project_id>/milestones", methods=["POST"])
@login_required
def add_milestone(project_id):
    db = get_db()
    project = owned(db, project_id)
    title = request.form.get("title", "").strip()[:60]
    amount = request.form.get("amount", type=int)
    if not title or not amount or not 0 < amount <= project["funding_needed"]:
        flash(f"A milestone needs a title and an amount up to ₹{project['funding_needed']:,}.")
    elif db.execute("SELECT COUNT(*) FROM project_milestones WHERE project_id = ?", (project_id,)).fetchone()[0] >= MAX_MILESTONES:
        flash(f"A project can have up to {MAX_MILESTONES} milestones.")
    else:
        db.execute("INSERT INTO project_milestones (project_id, title, amount) VALUES (?, ?, ?)", (project_id, title, amount))
        db.commit()
        check_milestones(db, project_id)
    live.touch(["posts"])
    return back_to_post(project_id)


@proj_bp.route("/<int:project_id>/milestones/<int:milestone_id>/<action>", methods=["POST"])
@login_required
def milestone_action(project_id, milestone_id, action):
    db = get_db()
    owned(db, project_id)
    if action == "done":
        db.execute("UPDATE project_milestones SET done = 1 - done WHERE id = ? AND project_id = ?", (milestone_id, project_id))
    elif action == "delete":
        db.execute("DELETE FROM project_milestones WHERE id = ? AND project_id = ?", (milestone_id, project_id))
    else:
        abort(404)
    db.commit()
    live.touch(["posts"])
    return back_to_post(project_id)


@proj_bp.route("/<int:project_id>/follow", methods=["POST"])
@login_required
def toggle_follow(project_id):
    db = get_db()
    me = session["user_id"]
    project = db.execute("SELECT innovator_id, title FROM projects WHERE id = ?", (project_id,)).fetchone()
    if project is None or project["innovator_id"] == me:
        abort(404)
    if db.execute("SELECT 1 FROM interests WHERE project_id = ? AND sponsor_id = ? AND status = 'accepted'", (project_id, me)).fetchone():
        flash("You sponsor this project, so you follow it automatically.")
    elif db.execute("DELETE FROM project_follows WHERE project_id = ? AND user_id = ?", (project_id, me)).rowcount:
        flash("You unfollowed this project.")
    else:
        db.execute("INSERT INTO project_follows (project_id, user_id) VALUES (?, ?)", (project_id, me))
        flash("Following. You'll get its updates and milestones.")
    db.commit()
    live.touch(["posts"])
    return back_to_post(project_id)
