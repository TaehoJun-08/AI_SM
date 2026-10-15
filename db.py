
#Standard Libraries import
import sqlite3
import os
import re

#Third Party Library import
from flask import g

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "db")
DB_PATH = os.path.join(DB_DIR, "aisponsor.db")
SCHEMA_PATH = os.path.join(DB_DIR, "schema.sql")

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def add_usernames(conn):
    """Older databases have no username column: add it and give everyone one."""
    columns = [row[1] for row in conn.execute("PRAGMA table_info(users)")]
    if not columns or "username" in columns:
        return
    conn.execute("ALTER TABLE users ADD COLUMN username TEXT")
    taken = set()
    for user_id, email in conn.execute("SELECT id, email FROM users ORDER BY id").fetchall():
        name = re.sub(r"[^a-z0-9_.]", "", email.split("@")[0].lower())[:16]
        if len(name) < 3 or name in taken:
            name = f"{name}{user_id}"
        if len(name) < 3:
            name = f"user{user_id}"
        taken.add(name)
        conn.execute("UPDATE users SET username = ? WHERE id = ?", (name, user_id))
        print(f"  {email} -> @{name}")


def add_bio(conn):
    """Older databases have no bio column: add it, empty for everyone."""
    columns = [row[1] for row in conn.execute("PRAGMA table_info(users)")]
    if columns and "bio" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN bio TEXT NOT NULL DEFAULT ''")


def add_post_columns(conn):
    """Older databases: posts get photo/video columns, sponsor offers get amount + message."""
    def columns(table):
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    project_cols = columns("projects")
    if project_cols and "media_path" not in project_cols:
        conn.execute("ALTER TABLE projects ADD COLUMN media_path TEXT")
        conn.execute("ALTER TABLE projects ADD COLUMN media_type TEXT")
    interest_cols = columns("interests")
    if interest_cols and "amount" not in interest_cols:
        conn.execute("ALTER TABLE interests ADD COLUMN amount INTEGER")
        conn.execute("ALTER TABLE interests ADD COLUMN message TEXT NOT NULL DEFAULT ''")


def add_user_settings(conn):
    """Older databases: profile photo + preference columns on users."""
    columns = [row[1] for row in conn.execute("PRAGMA table_info(users)")]
    if not columns:
        return
    wanted = {
        "avatar": "TEXT",
        "theme": "TEXT NOT NULL DEFAULT 'system'",
        "accent": "TEXT NOT NULL DEFAULT 'aurora'",
        "calm": "INTEGER NOT NULL DEFAULT 0",
        "autoplay": "INTEGER NOT NULL DEFAULT 1",
        "dm_policy": "TEXT NOT NULL DEFAULT 'everyone'",
        "accepts_offers": "INTEGER NOT NULL DEFAULT 1",
        "activity_seen": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'online'",
    }
    for name, definition in wanted.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
    interest_cols = [row[1] for row in conn.execute("PRAGMA table_info(interests)")]
    if interest_cols and "responded_at" not in interest_cols:
        conn.execute("ALTER TABLE interests ADD COLUMN responded_at TEXT")


def start_notifications(conn):
    """First run with notifications: everything that already exists counts as read."""
    conn.execute("UPDATE users SET activity_seen = datetime('now','localtime') WHERE activity_seen IS NULL")
    conn.execute("""INSERT OR IGNORE INTO dm_reads (thread_id, user_id, last_id)
                    SELECT t.id, u.uid, COALESCE((SELECT MAX(id) FROM dm_messages WHERE thread_id = t.id), 0)
                    FROM dm_threads t JOIN (SELECT user_a AS uid, id FROM dm_threads
                                            UNION SELECT user_b, id FROM dm_threads) u ON u.id = t.id""")
    conn.execute("""INSERT OR IGNORE INTO channel_reads (channel_id, user_id, last_id)
                    SELECT c.id, sm.user_id, COALESCE((SELECT MAX(id) FROM server_messages WHERE channel_id = c.id), 0)
                    FROM server_channels c JOIN server_members sm ON sm.server_id = c.server_id""")


def move_post_media(conn):
    """Posts used to hold one photo/video in projects.media_path: move it into post_media."""
    columns = [row[1] for row in conn.execute("PRAGMA table_info(projects)")]
    if "media_path" not in columns:
        return
    conn.execute("""INSERT INTO post_media (post_id, path, type, position)
                    SELECT id, media_path, media_type, 0 FROM projects
                    WHERE media_path IS NOT NULL AND media_type IS NOT NULL""")
    conn.execute("UPDATE projects SET media_path = NULL, media_type = NULL")


def needs_withdrawn_status(conn):
    """Old interests tables only allow pending/accepted/declined (a CHECK we can't alter)."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'interests'").fetchone()
    return row is not None and "withdrawn" not in row[0]


def add_reply_edit(conn):
    """Chat messages can be replies and can be edited: two new columns on both chat tables."""
    for table in ("dm_messages", "server_messages"):
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        if cols and "reply_to" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN reply_to INTEGER REFERENCES {table}(id) ON DELETE SET NULL")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN edited INTEGER NOT NULL DEFAULT 0")


def chat_needs_attachments(conn):
    """Old chat tables have no attachment columns and force a non-empty body (a CHECK we can't alter)."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(dm_messages)")]
    return bool(cols) and "attachment" not in cols


def add_server_features(conn):
    """Round two of servers: new columns on old tables, and a rebuild of server_channels
    (its kind CHECK can't be widened, and (server_id, name) can't stay unique because threads share names)."""
    def cols(table):
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    adds = {"users": [("is_bot", "INTEGER NOT NULL DEFAULT 0")],
            "servers": [("rules", "TEXT NOT NULL DEFAULT ''"), ("welcome", "TEXT NOT NULL DEFAULT ''"),
                        ("base_perms", "INTEGER NOT NULL DEFAULT 15"), ("goal_title", "TEXT NOT NULL DEFAULT ''"),
                        ("goal_amount", "INTEGER NOT NULL DEFAULT 0"),
                        ("project_id", "INTEGER REFERENCES projects(id) ON DELETE SET NULL")],
            "server_roles": [("perms", "INTEGER NOT NULL DEFAULT 0")],
            "server_members": [("nickname", "TEXT"), ("timeout_until", "TEXT"),
                               ("accepted_rules", "INTEGER NOT NULL DEFAULT 1")],
            "server_messages": [("pinned", "INTEGER NOT NULL DEFAULT 0"), ("embed", "TEXT")]}
    for table, new in adds.items():
        have = cols(table)
        for name, definition in new if have else []:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'server_channels'").fetchone()
    if row and "forum" not in row[0]:
        conn.execute("""CREATE TABLE server_channels_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
            category_id INTEGER REFERENCES server_categories(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'text' CHECK (kind IN ('text', 'announcement', 'voice', 'forum', 'thread')),
            topic TEXT NOT NULL DEFAULT '',
            is_private INTEGER NOT NULL DEFAULT 0 CHECK (is_private IN (0, 1)),
            position INTEGER NOT NULL DEFAULT 0,
            slowmode INTEGER NOT NULL DEFAULT 0,
            read_only INTEGER NOT NULL DEFAULT 0,
            parent_id INTEGER REFERENCES server_channels(id) ON DELETE CASCADE,
            parent_message_id INTEGER,
            created_by INTEGER)""")
        conn.execute("""INSERT INTO server_channels_new (id, server_id, category_id, name, kind, topic, is_private, position)
                        SELECT id, server_id, category_id, name, kind, topic, is_private, position FROM server_channels""")
        conn.execute("DROP TABLE server_channels")
        conn.execute("ALTER TABLE server_channels_new RENAME TO server_channels")


def init_db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    add_usernames(conn)
    add_bio(conn)
    add_post_columns(conn)
    add_user_settings(conn)
    add_server_features(conn)
    rebuild_chat = chat_needs_attachments(conn)
    if rebuild_chat:
        conn.execute("ALTER TABLE dm_messages RENAME TO dm_messages_old")
        conn.execute("ALTER TABLE server_messages RENAME TO server_messages_old")
    rebuild = needs_withdrawn_status(conn)
    if rebuild:
        conn.execute("ALTER TABLE interests RENAME TO interests_old")
    first_notifications = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'dm_reads'").fetchone() is None
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    if first_notifications:
        start_notifications(conn)
    move_post_media(conn)
    if rebuild_chat:
        conn.execute("""INSERT INTO dm_messages (id, thread_id, sender_id, body, created_at)
                        SELECT id, thread_id, sender_id, body, created_at FROM dm_messages_old""")
        conn.execute("""INSERT INTO server_messages (id, channel_id, sender_id, body, created_at)
                        SELECT id, channel_id, sender_id, body, created_at FROM server_messages_old""")
        conn.execute("DROP TABLE dm_messages_old")
        conn.execute("DROP TABLE server_messages_old")
        with open(SCHEMA_PATH, encoding="utf-8") as f:   # re-create the indexes that went with the old tables
            conn.executescript(f.read())
    if rebuild:
        conn.execute("""INSERT INTO interests (id, sponsor_id, project_id, status, amount, message, created_at)
                        SELECT id, sponsor_id, project_id, status, amount, message, created_at FROM interests_old""")
        conn.execute("DROP TABLE interests_old")
    add_reply_edit(conn)
    conn.commit()
    conn.close()
    print("Database ready at", DB_PATH)