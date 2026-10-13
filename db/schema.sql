CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name     TEXT    NOT NULL,
    username      TEXT    NOT NULL,
    email         TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'innovator',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users (username);

CREATE TABLE IF NOT EXISTS projects (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    innovator_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title          TEXT    NOT NULL,
    summary        TEXT    NOT NULL,
    description    TEXT    NOT NULL,
    domain         TEXT    NOT NULL,
    stage          TEXT    NOT NULL
                   CHECK (stage IN ('idea', 'prototype', 'mvp', 'growth')),
    funding_needed INTEGER NOT NULL CHECK (funding_needed > 0),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS sponsor_profiles (
    user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    organisation TEXT    NOT NULL,
    bio          TEXT    NOT NULL,
    budget_min   INTEGER NOT NULL CHECK (budget_min > 0),
    budget_max   INTEGER NOT NULL,
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (budget_max >= budget_min)
);

CREATE TABLE IF NOT EXISTS sponsor_domains (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    domain  TEXT    NOT NULL,
    PRIMARY KEY (user_id, domain)
);

CREATE TABLE IF NOT EXISTS interests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sponsor_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status      TEXT    NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'accepted', 'declined')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (sponsor_id, project_id)
);

CREATE TABLE IF NOT EXISTS project_members (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (project_id, user_id)
);

CREATE TABLE IF NOT EXISTS project_invites (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    code       TEXT    NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS channels (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    team_only  INTEGER NOT NULL DEFAULT 0 CHECK (team_only IN (0, 1)),
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (project_id, name)
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    sender_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body       TEXT    NOT NULL CHECK (length(body) BETWEEN 1 AND 2000),
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages (channel_id, id);

CREATE TABLE IF NOT EXISTS dm_threads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_a       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_b       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    requested_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status       TEXT    NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'accepted', 'declined')),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (user_a < user_b),
    UNIQUE (user_a, user_b)
);

CREATE TABLE IF NOT EXISTS dm_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id  INTEGER NOT NULL REFERENCES dm_threads(id) ON DELETE CASCADE,
    sender_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body       TEXT    NOT NULL CHECK (length(body) BETWEEN 1 AND 2000),
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_dm_messages_thread ON dm_messages (thread_id, id);