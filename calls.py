"""Who is in which voice/video call right now. In memory, like presence: it describes live connections.
A call is identified by a key: "dm-<thread id>" or "ch-<channel id>"."""
import time

_calls = {}   # key -> {"members": {sid: info}, "ring": {...} | None, "connected_at": float | None, "video": bool}


def get(key):
    return _calls.get(key)


def members(key):
    call = _calls.get(key)
    return call["members"] if call else {}


def count(key):
    return len(members(key))


def start(key, video=False):
    return _calls.setdefault(key, {"members": {}, "ring": None, "connected_at": None, "video": video})


def add(key, sid, info):
    call = start(key)
    call["members"][sid] = info
    if len(call["members"]) >= 2 and call["connected_at"] is None:
        call["connected_at"] = time.time()
    return call


def remove(key, sid):
    """Returns (info of the person who left, the call if it still has people else None)."""
    call = _calls.get(key)
    if not call:
        return None, None
    info = call["members"].pop(sid, None)
    return info, call


def finish(key):
    return _calls.pop(key, None)


def keys_of(sid):
    return [k for k, c in _calls.items() if sid in c["members"]]


def user_in(key, user_id):
    return any(m["uid"] == user_id for m in members(key).values())


def duration_text(seconds):
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}h {m}m" if h else (f"{m}m {s:02d}s" if m else f"{s}s")
