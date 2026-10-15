"""Real-time pushes. Every logged-in page keeps one socket open and joins two rooms:
`user-<id>` (things only that person needs, like their inbox) and `feed` (everyone).
Routes call these helpers after they commit, and static/js/live.js updates the page."""
from flask import Blueprint, session, jsonify, request
from flask_socketio import join_room
from extensions import socketio
from db import get_db
from helpers import login_required
import unread
import presence
import mutes

live_bp = Blueprint("live", __name__, url_prefix="/live")


@live_bp.route("/counts")
@login_required
def counts():
    """The numbers on the red badges, for pages that are already open."""
    return jsonify(unread.all_counts(session["user_id"]))


@live_bp.route("/status", methods=["POST"])
@login_required
def set_status():
    """Pick online / idle / do not disturb / invisible."""
    status = request.form.get("status", "")
    if status not in presence.STATUSES:
        return jsonify(error="Unknown status"), 400
    me = session["user_id"]
    presence.set_preference(me, status)
    announce_presence(me)
    return jsonify(status=status)


@live_bp.route("/mute", methods=["POST"])
@login_required
def mute():
    """Mute or unmute a chat, channel or server for the current person."""
    from dms.routes import get_thread
    from servers.routes import channel_for, membership
    me = session["user_id"]
    kind, target = request.form.get("kind", ""), request.form.get("id", type=int)
    allowed = {"dm": lambda: get_thread(target, me),
               "channel": lambda: channel_for(target, me)[1],
               "server": lambda: membership(target, me)}
    if kind not in allowed or target is None or not allowed[kind]():
        return jsonify(error="Not found"), 404
    muted = mutes.toggle(me, kind, target)
    refresh_badges(me)
    if kind == "dm":
        inbox_changed(target)
    return jsonify(muted=muted)


# ---------- "something changed, look again" ----------

def touch(topics, users=None):
    """Pages mark parts of themselves with data-region / data-live="topic ...". A touch on a topic
    makes every open page re-fetch itself and swap those parts. `users=None` tells everybody."""
    data = {"topics": list(topics)}
    if users is None:
        to_feed("touch", data)
    else:
        to_users(users, "touch", data)


def user_changed(user_id):
    """Name, @username or photo changed: every place that shows this person redraws."""
    db = get_db()
    row = db.execute("SELECT id, username, full_name, avatar FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        return
    to_feed("user_changed", dict(row))
    touch(["users"])
    for t in db.execute("SELECT id FROM dm_threads WHERE ? IN (user_a, user_b)", (user_id,)).fetchall():
        inbox_changed(t["id"])
    for s in db.execute("SELECT server_id FROM server_members WHERE user_id = ?", (user_id,)).fetchall():
        server_changed(s["server_id"])


# ---------- typing ("X is typing…") ----------

@socketio.on("typing")
def on_typing(data):
    from flask_socketio import emit
    from dms.routes import get_thread, can_send
    from servers.routes import channel_for, can_write
    user_id = session.get("user_id")
    kind, target = data.get("kind"), data.get("id")
    if not user_id or not isinstance(target, int):
        return
    if kind == "dm":
        thread = get_thread(target, user_id)
        if thread is None or not can_send(thread, user_id):
            return
        room = f"dm-{target}"
    elif kind == "srv":
        m, channel = channel_for(target, user_id)
        if channel is None or not can_write(m, channel):
            return
        room = f"srv-{target}"
    else:
        return
    name = username(get_db(), user_id)
    emit("typing", {"user_id": user_id, "name": name, "kind": kind, "id": target}, to=room, include_self=False)


# ---------- who is online ----------

def announce_presence(user_id):
    to_feed("presence", {"id": user_id, "status": presence.status_of(user_id)})


def _announce_later(user_id):
    socketio.sleep(presence.GRACE + 0.5)      # they may just be moving to another page
    if not presence.is_connected(user_id):
        announce_presence(user_id)


@socketio.on("connect")
def on_connect():
    user_id = session.get("user_id")
    if user_id:
        join_room(f"user-{user_id}")
        join_room("feed")
        presence.connect(user_id, request.sid)
        announce_presence(user_id)


@socketio.on("disconnect")
def on_disconnect():
    user_id = session.get("user_id")
    if user_id:
        presence.preference(user_id)          # load it now: the background task has no request
        presence.disconnect(user_id, request.sid)
        import callsvc
        callsvc.drop_connection(request.sid)    # closing the call window hangs up
        socketio.start_background_task(_announce_later, user_id)


@socketio.on("presence_away")
def on_away(data):
    user_id = session.get("user_id")
    if user_id:
        presence.set_away(user_id, bool(data.get("away")))
        announce_presence(user_id)


def to_users(user_ids, event, data=None):
    for user_id in set(user_ids):
        socketio.emit(event, data or {}, to=f"user-{user_id}")


def to_feed(event, data):
    socketio.emit(event, data, to="feed")


# ---------- notifications (pop-ups + badges) ----------

ATTACHMENT_LABELS = {"image": "📷 Photo", "video": "🎥 Video", "audio": "🎵 Audio"}


def preview(body, attach_type=None):
    """One line describing a chat message, for pop-ups."""
    return (body or "").strip() or ATTACHMENT_LABELS.get(attach_type, "📎 File")


def username(db, user_id):
    row = db.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["username"] if row else "someone"


def notify(user_ids, kind, title, body, url, key="", mute=()):
    """Pop-up for these people. `key` lets a page that is already showing the thing stay quiet.
    `mute` lists the (kind, id) pairs that silence it. Do-not-disturb silences everything."""
    data = {"kind": kind, "title": title, "body": body[:140], "url": url, "key": key}
    for user_id in set(user_ids):
        if presence.preference(user_id) == "dnd" or any(mutes.is_muted(user_id, k, i) for k, i in mute):
            refresh_badges(user_id)           # no pop-up, the red badge is still up to date
        else:
            to_users([user_id], "notify", data)


def refresh_badges(user_id):
    to_users([user_id], "badges")


def notify_dm(db, thread_id, sender_id, text):
    row = db.execute("SELECT * FROM dm_threads WHERE id = ?", (thread_id,)).fetchone()
    if row is None:
        return
    other = row["user_b"] if row["user_a"] == sender_id else row["user_a"]
    if row["status"] == "declined" and row["requested_by"] != other:
        return                                             # they declined this person: stay silent
    request = row["status"] == "pending" and row["requested_by"] != other
    name = username(db, sender_id)
    notify([other], "dm", ("Message request · @" if request else "@") + name, text,
           f"/messages/{thread_id}", key=f"dm:{thread_id}", mute=[("dm", thread_id)])


def notify_channel(db, channel, sender_id, text, skip=()):
    from servers.routes import membership, can_read
    server = db.execute("SELECT name FROM servers WHERE id = ?", (channel["server_id"],)).fetchone()
    name = username(db, sender_id)
    people = []
    for r in db.execute("SELECT user_id FROM server_members WHERE server_id = ?", (channel["server_id"],)):
        m = membership(channel["server_id"], r["user_id"])
        if r["user_id"] != sender_id and r["user_id"] not in skip and m and not m["pending_rules"] and can_read(m, channel):
            people.append(r["user_id"])
    notify(people, "server", f"#{channel['name']} · {server['name']}", f"@{name}: {text}",
           f"/servers/{channel['server_id']}/{channel['id']}", key=f"srv:{channel['id']}",
           mute=[("channel", channel["id"]), ("server", channel["server_id"])])


# ---------- direct messages ----------

def inbox_changed(thread_id, reload=False):
    """Both people's chat lists should redraw (new message, edit, delete, accepted request...)."""
    row = get_db().execute("SELECT user_a, user_b FROM dm_threads WHERE id = ?", (thread_id,)).fetchone()
    if row:
        to_users([row["user_a"], row["user_b"]], "inbox", {"thread_id": thread_id, "reload": reload})


def dm_note(db, thread_id, message_id, notify=True):
    """A message the server wrote (sponsorship notes): show it in the open chat and the lists."""
    from dms.routes import dm_message
    message = dm_message(db, message_id)
    socketio.emit("message", message, to=f"dm-{thread_id}")
    inbox_changed(thread_id)
    if notify:
        notify_dm(db, thread_id, message["sender_id"], preview(message["body"], message["attach_type"]))


# ---------- sponsorship ----------

def offer_changed(post_id, *user_ids):
    """Funding bar, Sponsor button and the activity pages all need a fresh look."""
    to_feed("post_changed", {"id": post_id})
    to_users(user_ids, "activity")
    touch(["engagement"])


# ---------- servers ----------

def server_changed(server_id, extra_people=()):
    """Channels, roles or members changed: everyone in the server (and anyone just removed) redraws."""
    rows = get_db().execute("SELECT user_id FROM server_members WHERE server_id = ?", (server_id,))
    to_users([r["user_id"] for r in rows] + list(extra_people), "server_changed", {"server_id": server_id})
