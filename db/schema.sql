CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name     TEXT    NOT NULL,
    email         TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'innovator',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

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