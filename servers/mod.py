"""Moderation and membership housekeeping: kick, ban, timeout, nicknames, rules screen, invites, safety."""
import secrets
from datetime import datetime, timedelta
from flask import render_template, request, redirect, url_for, flash, session, abort
from db import get_db
from helpers import login_required
import live
from servers.routes import (servers_bp, membership, outranks, need, back, audit, now_text, my_servers,
                            valid_invite, join_server)


def done(server_id, tab):
    """Back to where the action came from (a member card) or to the settings tab."""
    target = request.form.get("next", "")
    if target.startswith("/servers/") and "//" not in target:
        return redirect(target)
    return back(server_id, tab)


def target_for(server_id, user_id, *wanted):
    """(my membership, target membership) if I hold one of the powers and outrank the target, else flash + None."""
    m = need(server_id, *wanted)
    target = membership(server_id, user_id)
    if target is None:
        abort(404)
    db = get_db()
    is_bot = db.execute("SELECT is_bot FROM users WHERE id = ?", (user_id,)).fetchone()["is_bot"]
    if user_id == session["user_id"] or is_bot or not outranks(m, target):
        flash("You can't do that to this member (companions are managed in the Companions tab).")
        return m, None
    return m, target


@servers_bp.route("/<int:server_id>/members/<int:user_id>/kick", methods=["POST"])
@login_required
def kick(server_id, user_id):
    m, target = target_for(server_id, user_id, "kick")
    if target:
        db = get_db()
        db.execute("DELETE FROM server_members WHERE server_id = ? AND user_id = ?", (server_id, user_id))
        audit(server_id, "member.kick", live.username(db, user_id))
        db.commit()
        live.notify([user_id], "server", "Removed from a server",
                    f"You were removed from {m['server']['name']}", "/servers/")
        flash("Member removed.")
    return done(server_id, "members")


@servers_bp.route("/<int:server_id>/members/<int:user_id>/ban", methods=["POST"])
@login_required
def ban(server_id, user_id):
    m, target = target_for(server_id, user_id, "ban")
    if target:
        db = get_db()
        reason = request.form.get("reason", "").strip()[:200]
        db.execute("DELETE FROM server_members WHERE server_id = ? AND user_id = ?", (server_id, user_id))
        db.execute("INSERT OR REPLACE INTO server_bans (server_id, user_id, reason, banned_by) VALUES (?, ?, ?, ?)",
                   (server_id, user_id, reason, session["user_id"]))
        audit(server_id, "member.ban", f"{live.username(db, user_id)} {reason}".strip())
        db.commit()
        live.notify([user_id], "server", "Banned from a server",
                    f"You were banned from {m['server']['name']}", "/servers/")
        flash("Member banned.")
    return done(server_id, "members")


@servers_bp.route("/<int:server_id>/bans/<int:user_id>/unban", methods=["POST"])
@login_required
def unban(server_id, user_id):
    need(server_id, "ban")
    db = get_db()
    db.execute("DELETE FROM server_bans WHERE server_id = ? AND user_id = ?", (server_id, user_id))
    audit(server_id, "member.unban", live.username(db, user_id))
    db.commit()
    flash("Ban lifted.")
    return back(server_id, "safety")


@servers_bp.route("/<int:server_id>/members/<int:user_id>/timeout", methods=["POST"])
@login_required
def timeout(server_id, user_id):
    m, target = target_for(server_id, user_id, "timeout")
    if target:
        db = get_db()
        minutes = min(max(request.form.get("minutes", 0, type=int) or 0, 0), 60 * 24 * 28)
        until = (datetime.now() + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S") if minutes else None
        db.execute("UPDATE server_members SET timeout_until = ? WHERE server_id = ? AND user_id = ?",
                   (until, server_id, user_id))
        audit(server_id, "member.timeout" if minutes else "member.untimeout",
              f"{live.username(db, user_id)} {minutes} min" if minutes else live.username(db, user_id))
        db.commit()
        if minutes:
            live.notify([user_id], "server", "Timed out", f"You can't talk in {m['server']['name']} for {minutes} min",
                        f"/servers/{server_id}")
        flash(f"Timed out for {minutes} minutes." if minutes else "Timeout lifted.")
    return done(server_id, "members")


@servers_bp.route("/<int:server_id>/members/<int:user_id>/nick", methods=["POST"])
@login_required
def nickname(server_id, user_id):
    m = need(server_id)
    me = session["user_id"]
    if user_id != me:
        target = membership(server_id, user_id)
        if target is None or "manage_roles" not in m["perms"] or not outranks(m, target):
            abort(404)
    nick = " ".join(request.form.get("nickname", "").split())[:32] or None
    db = get_db()
    db.execute("UPDATE server_members SET nickname = ? WHERE server_id = ? AND user_id = ?", (nick, server_id, user_id))
    if user_id != me:
        audit(server_id, "member.nick", f"{live.username(db, user_id)} → {nick or 'reset'}")
    db.commit()
    flash("Nickname saved." if nick else "Nickname cleared.")
    return done(server_id, "members")


# ---------- rules screen ----------

@servers_bp.route("/<int:server_id>/rules", methods=["GET", "POST"])
@login_required
def rules_page(server_id):
    from servers import companions
    m = membership(server_id, session["user_id"])
    if m is None:
        abort(404)
    if request.method == "POST" or not m["pending_rules"]:
        if m["pending_rules"]:
            db = get_db()
            db.execute("UPDATE server_members SET accepted_rules = 1 WHERE server_id = ? AND user_id = ?",
                       (server_id, session["user_id"]))
            db.commit()
            companions.on_join(db, server_id, session["user_id"])
        return redirect(url_for("servers.view", server_id=server_id))
    return render_template("servers/rules.html", rail=my_servers(session["user_id"]), server=m["server"])


# ---------- safety (rules + welcome note) ----------

@servers_bp.route("/<int:server_id>/safety", methods=["POST"])
@login_required
def save_safety(server_id):
    need(server_id, "manage_server")
    db = get_db()
    db.execute("UPDATE servers SET rules = ?, welcome = ? WHERE id = ?",
               (request.form.get("rules", "").strip()[:2000], request.form.get("welcome", "").strip()[:300], server_id))
    audit(server_id, "server.safety")
    db.commit()
    flash("Saved. New members must agree to the rules before they can chat." if request.form.get("rules", "").strip()
          else "Saved. New members join straight away.")
    return back(server_id, "safety")


# ---------- invites ----------

@servers_bp.route("/<int:server_id>/invites", methods=["POST"])
@login_required
def create_invite(server_id):
    need(server_id, "invite", "manage_server")
    hours = request.form.get("hours", 0, type=int) or 0
    uses = request.form.get("max_uses", 0, type=int) or 0
    expires = (datetime.now() + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S") if hours > 0 else None
    code = secrets.token_urlsafe(6).replace("-", "x").replace("_", "y").upper()[:8]
    db = get_db()
    db.execute("INSERT INTO server_invites (code, server_id, created_by, max_uses, expires_at) VALUES (?, ?, ?, ?, ?)",
               (code, server_id, session["user_id"], max(uses, 0), expires))
    audit(server_id, "invite.create", code)
    db.commit()
    flash(f"Invite {code} created.")
    return back(server_id, "invite")


@servers_bp.route("/<int:server_id>/invites/<code>/delete", methods=["POST"])
@login_required
def delete_invite(server_id, code):
    need(server_id, "manage_server")
    db = get_db()
    db.execute("DELETE FROM server_invites WHERE code = ? AND server_id = ?", (code, server_id))
    audit(server_id, "invite.delete", code)
    db.commit()
    return back(server_id, "invite")


@servers_bp.route("/invite/<code>", methods=["GET", "POST"])
@login_required
def invite_page(code):
    """Landing page for an invite link: see the server, press Join."""
    db = get_db()
    server_id, invite = valid_invite(db, code.upper())
    if server_id is None:
        flash("That invite doesn't exist, has expired, or is used up.")
        return redirect(url_for("servers.home"))
    if request.method == "POST":
        return join_server(server_id, invite)
    server = db.execute(
        """SELECT s.*, (SELECT COUNT(*) FROM server_members WHERE server_id = s.id) AS members
           FROM servers s WHERE s.id = ?""", (server_id,)).fetchone()
    joined = membership(server_id, session["user_id"]) is not None
    from servers.routes import join_preview
    return render_template("servers/invite.html", rail=my_servers(session["user_id"]), server=server,
                           code=code, joined=joined, action="", p=join_preview(db, server))
