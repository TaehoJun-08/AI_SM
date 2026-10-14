"""Voice and video calls. The audio/video itself flows straight between browsers (WebRTC);
this file only introduces the people to each other (signalling), rings the other person,
and writes a "call" line into the chat when it ends."""
import time
from flask import Blueprint, render_template, request, session, abort
from flask_socketio import join_room, leave_room, emit
from extensions import socketio
from db import get_db
from helpers import login_required
import calls
import live
import presence

calls_bp = Blueprint("calls", __name__, url_prefix="/call")

RING_SECONDS = 45


def access(key, user_id):
    """Who may be in this call? Returns a dict describing it, or None."""
    from dms.routes import get_thread
    from servers.routes import channel_for
    db = get_db()
    kind, _, raw = key.partition("-")
    if not raw.isdigit():
        return None
    target = int(raw)
    if kind == "dm":
        thread = get_thread(target, user_id)
        if thread is None or thread["status"] != "accepted":
            return None
        other = thread["user_b"] if thread["user_a"] == user_id else thread["user_a"]
        row = db.execute("SELECT id, username, full_name, avatar FROM users WHERE id = ?", (other,)).fetchone()
        return {"kind": "dm", "title": row["username"], "other": row, "thread_id": target,
                "back": f"/messages/{target}", "room": f"call-{key}"}
    if kind == "ch":
        m, channel = channel_for(target, user_id)
        if channel is None:
            return None
        return {"kind": "ch", "title": f"#{channel['name']} · {m['server']['name']}", "other": None,
                "channel": channel, "back": f"/servers/{channel['server_id']}/{channel['id']}", "room": f"call-{key}"}
    return None


@calls_bp.route("/<key>")
@login_required
def room(key):
    info = access(key, session["user_id"])
    if info is None:
        abort(404)
    user = get_db().execute("SELECT id, username, full_name, avatar FROM users WHERE id = ?",
                            (session["user_id"],)).fetchone()
    return render_template("calls/room.html", key=key, info=info, me=user,
                           want_video=request.args.get("video") == "1", ring=request.args.get("ring") == "1")


# ---------- signalling ----------

def person_info(user_id):
    row = get_db().execute("SELECT username, full_name, avatar FROM users WHERE id = ?", (user_id,)).fetchone()
    return {"uid": user_id, "name": row["username"], "avatar": row["avatar"], "muted": False, "video": False,
            "sharing": False}


def roster(key):
    """Channel pages show how many people are in the call."""
    if key.startswith("ch-"):
        socketio.emit("call_roster", {"key": key, "count": calls.count(key)}, to=f"srv-{key[3:]}")


@socketio.on("call_join")
def on_join(data):
    user_id = session.get("user_id")
    key = str(data.get("key", ""))
    if not user_id or access(key, user_id) is None:
        emit("call_failed", {"reason": "You can't join this call."})
        return
    # one place per person: a second tab replaces the first
    for sid, m in list(calls.members(key).items()):
        if m["uid"] == user_id and sid != request.sid:
            emit("call_replaced", {}, to=sid)
            drop_member(key, sid)
    info = person_info(user_id)
    info["video"] = bool(data.get("video"))
    others = [{"sid": sid, **m} for sid, m in calls.members(key).items()]
    call = calls.add(key, request.sid, info)
    join_room(f"call-{key}")
    presence.set_incall(user_id, True)
    live.announce_presence(user_id)
    emit("call_peers", {"peers": others})
    emit("call_peer_joined", {"peer": {"sid": request.sid, **info}}, to=f"call-{key}", include_self=False)
    ring = call.get("ring")
    if ring and ring["callee"] == user_id:                 # they picked up
        ring["answered"] = True
        live.to_users([user_id], "call_cancel", {"key": key})   # hide the ringing screen in other tabs
    roster(key)


def drop_member(key, sid):
    """Take one connection out of a call and tidy up if it was the last."""
    info, call = calls.remove(key, sid)
    if info is None:
        return
    leave_room(f"call-{key}", sid=sid)
    if not calls.user_in(key, info["uid"]):
        presence.set_incall(info["uid"], False)
        live.announce_presence(info["uid"])
    socketio.emit("call_peer_left", {"sid": sid}, to=f"call-{key}")
    roster(key)
    if call is not None and not call["members"]:
        end_call(key, call)


def end_call(key, call):
    calls.finish(key)
    ring = call.get("ring")
    if key.startswith("dm-"):
        thread_id = int(key[3:])
        db = get_db()
        video = "video" if call.get("video") else "voice"
        if call["connected_at"]:
            text = f"{'📹' if video == 'video' else '📞'} {video.capitalize()} call · {calls.duration_text(time.time() - call['connected_at'])}"
            caller = ring["caller"] if ring else None
            notify = False
        elif ring:
            text = f"📵 Missed {video} call"
            caller = ring["caller"]
            notify = True
            live.to_users([ring["callee"]], "call_cancel", {"key": key})
        else:
            return
        if caller:
            note = db.execute("INSERT INTO dm_messages (thread_id, sender_id, body) VALUES (?, ?, ?)",
                              (thread_id, caller, text)).lastrowid
            db.commit()
            live.dm_note(db, thread_id, note, notify=notify)


@socketio.on("call_leave")
def on_leave(data):
    key = str(data.get("key", ""))
    if request.sid in calls.members(key):
        drop_member(key, request.sid)


def drop_connection(sid):
    """A browser closed (called from live.on_disconnect)."""
    for key in calls.keys_of(sid):
        drop_member(key, sid)


@socketio.on("call_signal")
def on_signal(data):
    key, to = str(data.get("key", "")), data.get("to")
    members = calls.members(key)
    if request.sid in members and to in members:
        emit("call_signal", {"from": request.sid, "data": data.get("data")}, to=to)


@socketio.on("call_state")
def on_state(data):
    key = str(data.get("key", ""))
    me = calls.members(key).get(request.sid)
    if me is None:
        return
    for field in ("muted", "video", "sharing"):
        if field in data:
            me[field] = bool(data[field])
    emit("call_peer_state", {"sid": request.sid, "muted": me["muted"], "video": me["video"], "sharing": me["sharing"]},
         to=f"call-{key}", include_self=False)


# ---------- ringing ----------

@socketio.on("call_invite")
def on_invite(data):
    """The caller (already on the call page) rings the other person of a DM."""
    user_id = session.get("user_id")
    key = str(data.get("key", ""))
    info = access(key, user_id) if user_id else None
    if info is None or info["kind"] != "dm" or request.sid not in calls.members(key):
        return
    callee = info["other"]["id"]
    video = bool(data.get("video"))
    if calls.user_in(key, callee):
        return                                              # already in it
    if presence.status_of(callee) == "offline":
        emit("call_failed", {"reason": f"@{info['other']['username']} is offline."})
        return
    if presence.in_call(callee):
        emit("call_failed", {"reason": f"@{info['other']['username']} is on another call."})
        return
    if presence.preference(callee) == "dnd":
        emit("call_failed", {"reason": f"@{info['other']['username']} has Do Not Disturb on."})
        return
    call = calls.get(key)
    call["video"] = video
    call["ring"] = {"caller": user_id, "callee": callee, "answered": False}
    me = person_info(user_id)
    live.to_users([callee], "call_ring", {"key": key, "video": video, "from": {"id": user_id, "name": me["name"], "avatar": me["avatar"]},
                                          "url": f"/call/{key}"})
    emit("call_ringing", {})
    socketio.start_background_task(_ring_timeout, key, call["ring"])


def _ring_timeout(key, ring):
    socketio.sleep(RING_SECONDS)
    call = calls.get(key)
    if call and call.get("ring") is ring and not ring["answered"]:
        socketio.emit("call_failed", {"reason": "No answer."}, to=f"call-{key}")
        live.to_users([ring["callee"]], "call_cancel", {"key": key})


@socketio.on("call_decline")
def on_decline(data):
    user_id = session.get("user_id")
    key = str(data.get("key", ""))
    call = calls.get(key)
    ring = call.get("ring") if call else None
    if ring and ring["callee"] == user_id:
        live.to_users([user_id], "call_cancel", {"key": key})
        socketio.emit("call_failed", {"reason": "Declined."}, to=f"call-{key}")
