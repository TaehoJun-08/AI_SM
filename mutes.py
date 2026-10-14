"""Muted chats, channels and servers: no pop-ups and no badges for them."""
from db import get_db

KINDS = ("dm", "channel", "server")


def is_muted(user_id, kind, target_id):
    if not user_id:
        return False
    return get_db().execute("SELECT 1 FROM mutes WHERE user_id = ? AND kind = ? AND target_id = ?",
                            (user_id, kind, target_id)).fetchone() is not None


def toggle(user_id, kind, target_id):
    """Flip the mute and return whether it is now muted."""
    db = get_db()
    removed = db.execute("DELETE FROM mutes WHERE user_id = ? AND kind = ? AND target_id = ?",
                         (user_id, kind, target_id)).rowcount
    if not removed:
        db.execute("INSERT INTO mutes (user_id, kind, target_id) VALUES (?, ?, ?)", (user_id, kind, target_id))
    db.commit()
    return not removed
