"""Who is online. A person is online while at least one of their pages has a socket open.
Their chosen status (online / idle / do not disturb / invisible) decides how they appear to others.
State lives in memory: it describes who is connected right now, so a restart simply starts empty."""
import time
from db import get_db

STATUSES = ("online", "idle", "dnd", "invisible")
GRACE = 6          # seconds: moving between pages closes one socket and opens another

_sockets = {}      # user_id -> {socket ids}
_away = set()      # users whose open pages have all been left alone for a while
_incall = set()    # users currently in a voice/video call
_gone_at = {}      # user_id -> when their last socket closed
_prefs = {}        # user_id -> chosen status (cache of users.status)


def preference(user_id):
    """The status the person picked (what they see for themselves)."""
    if user_id not in _prefs:
        row = get_db().execute("SELECT status FROM users WHERE id = ?", (user_id,)).fetchone()
        _prefs[user_id] = row["status"] if row else "online"
    return _prefs[user_id]


def set_preference(user_id, status):
    get_db().execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
    get_db().commit()
    _prefs[user_id] = status


def is_connected(user_id):
    return bool(_sockets.get(user_id)) or time.time() - _gone_at.get(user_id, 0) < GRACE


def status_of(user_id):
    """How others see this person: online, idle, dnd or offline."""
    pref = preference(user_id)
    if pref == "invisible" or not is_connected(user_id):
        return "offline"
    if user_id in _incall:
        return "call"
    if pref == "dnd":
        return "dnd"
    if pref == "idle" or user_id in _away:
        return "idle"
    return "online"


def connect(user_id, sid):
    _sockets.setdefault(user_id, set()).add(sid)
    _gone_at.pop(user_id, None)


def disconnect(user_id, sid):
    sockets = _sockets.get(user_id, set())
    sockets.discard(sid)
    if not sockets:
        _sockets.pop(user_id, None)
        _away.discard(user_id)
        _gone_at[user_id] = time.time()


def set_away(user_id, away):
    (_away.add if away else _away.discard)(user_id)


def set_incall(user_id, value):
    (_incall.add if value else _incall.discard)(user_id)


def in_call(user_id):
    return user_id in _incall
