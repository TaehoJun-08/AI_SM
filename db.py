
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


def init_db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    add_usernames(conn)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    print("Database ready at", DB_PATH)