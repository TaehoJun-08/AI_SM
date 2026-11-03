
#Standard Libraries import
import os
import re
from datetime import datetime

#Third Party Library import
from dotenv import load_dotenv
from flask import g
import psycopg2
import psycopg2.extras
import psycopg2.pool

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "db")
SCHEMA_PATH = os.path.join(DB_DIR, "schema.sql")
load_dotenv(os.path.join(BASE_DIR, ".env"))   # every entry point needs DATABASE_URL -- don't rely on each one loading it first
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres@127.0.0.1:5432/postgres")

# Postgres's own server process has its own TimeZone setting (defaults to GMT here), which has
# nothing to do with this machine's local time -- unlike sqlite3, which never had a separate
# server process and so never had this gotcha. Every LOCALTIMESTAMP / to_char(...) call in the
# app's SQL (cooldowns, expiries, "N days ago" style timestamps) assumes it means *this computer's*
# local time, so every connection is pinned to the OS's real UTC offset right away.
_offset_minutes = int(datetime.now().astimezone().utcoffset().total_seconds() // 60)
_sign = "+" if _offset_minutes >= 0 else "-"
TZ_OFFSET = f"{_sign}{abs(_offset_minutes) // 60:02d}:{abs(_offset_minutes) % 60:02d}"


def _pin_local_timezone(conn):
    cur = conn.cursor()
    cur.execute(f"SET TIME ZONE INTERVAL '{TZ_OFFSET}' HOUR TO MINUTE")
    conn.commit()

# A new TCP connection per request is expensive under real concurrency (Postgres forks a
# backend process per connection) -- every per-request get_db() call borrows one of these
# instead. Background work that can outlive a request (Nova's streaming replies, the
# companion worker) still gets its own dedicated connection via open_connection().
_pool = psycopg2.pool.ThreadedConnectionPool(2, 40, DATABASE_URL)

# Tables whose primary key is a plain serial "id" column. Every call site that wants
# cur.lastrowid (the sqlite3 idiom this codebase was written against) targets one of
# these -- junction tables with composite keys never do, so we only add "RETURNING id"
# here and never risk it on a table that has no such column.
TABLES_WITH_ID = {
    "users", "projects", "interests", "channels", "messages", "dm_threads", "dm_messages",
    "comments", "servers", "server_roles", "server_categories", "server_channels",
    "server_messages", "post_media", "audit_log", "bots", "reminders", "server_emojis",
    "server_events", "project_updates", "project_milestones", "pending_posts", "deal_rounds",
    "ai_messages", "ai_memory", "ai_actions", "project_endorsements", "deal_coach_messages",
}

_NAMED_PARAM = re.compile(r":(?!:)([a-z_][a-zA-Z0-9_]*)")
_INSERT_TABLE = re.compile(r"(?i)^\s*INSERT\s+INTO\s+([a-zA-Z_]+)")


def _translate(sql):
    """This codebase is written in sqlite3's placeholder style (?  and :name).
    psycopg2 only understands %s and %(name)s, so every query is rewritten once here
    instead of touching the 500+ call sites that already use the sqlite style."""
    sql = _NAMED_PARAM.sub(r"%(\1)s", sql)
    return sql.replace("?", "%s")


class Row(dict):
    """Behaves like sqlite3.Row: supports row["col"] (used everywhere) and row[0]
    (used by the many `.fetchone()[0]` call sites), and still satisfies the mapping
    protocol so dict(row) and Jinja's `row.col` attribute fallback keep working."""

    def __init__(self, data):
        super().__init__(data)
        self._order = list(data.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return super().__getitem__(self._order[key])
        return super().__getitem__(key)


class Cursor:
    def __init__(self, cur, lastrowid=None):
        self._cur = cur
        self.lastrowid = lastrowid

    def fetchone(self):
        row = self._cur.fetchone()
        return Row(row) if row is not None else None

    def fetchall(self):
        return [Row(r) for r in self._cur.fetchall()]

    def __iter__(self):
        for r in self._cur:
            yield Row(r)

    @property
    def rowcount(self):
        return self._cur.rowcount


class Connection:
    def __init__(self, conn, pooled=False):
        self._conn = conn
        self._pooled = pooled

    def execute(self, sql, params=()):
        translated = _translate(sql)
        match = _INSERT_TABLE.match(translated)
        table = match.group(1) if match else None
        wants_returning = table in TABLES_WITH_ID and "RETURNING" not in translated.upper()
        if wants_returning:
            translated = translated.rstrip().rstrip(";") + " RETURNING id"
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Passing even an empty params tuple makes psycopg2 treat the query as a %-format string,
        # which breaks on a literal "%" (e.g. a LIKE pattern with no bound parameter at all).
        # sqlite3 never had this gotcha since it only binds "?", never %-interpolates.
        if params:
            cur.execute(translated, params)
        else:
            cur.execute(translated)
        lastrowid = None
        if wants_returning:
            row = cur.fetchone()
            lastrowid = row["id"] if row else None
        return Cursor(cur, lastrowid)

    def executemany(self, sql, seq_of_params):
        cur = self._conn.cursor()
        cur.executemany(_translate(sql), list(seq_of_params))
        return Cursor(cur)

    def executescript(self, sql):
        cur = self._conn.cursor()
        cur.execute(sql)
        self._conn.commit()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if self._pooled:
            self._conn.rollback()      # never hand back a connection mid-transaction
            _pool.putconn(self._conn)
        else:
            self._conn.close()


def open_connection():
    """A standalone connection, independent of any Flask request. Use this (not get_db)
    for background work that outlives the request -- Nova's streaming replies, the
    companion worker, and one-off scripts like seed_demo.py."""
    conn = psycopg2.connect(DATABASE_URL)
    _pin_local_timezone(conn)
    return Connection(conn)


def get_db():
    if "db" not in g:
        # Under a sudden burst the pool can be briefly fully checked out; wait for one
        # to free up instead of failing the request outright.
        import time
        for attempt in range(50):
            try:
                raw = _pool.getconn()
                _pin_local_timezone(raw)   # cheap, and correct whether this connection is new or reused
                g.db = Connection(raw, pooled=True)
                break
            except psycopg2.pool.PoolError:
                if attempt == 49:
                    raise
                time.sleep(0.1)
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = open_connection()
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.close()
    print("Database ready (Postgres) at", DATABASE_URL)
