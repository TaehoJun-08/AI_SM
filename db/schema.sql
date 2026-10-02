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