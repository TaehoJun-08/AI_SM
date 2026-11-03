CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    full_name     TEXT    NOT NULL,
    username      TEXT    NOT NULL,
    email         TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'innovator',
    bio           TEXT    NOT NULL DEFAULT '',
    avatar        TEXT,
    theme         TEXT    NOT NULL DEFAULT 'system',
    accent        TEXT    NOT NULL DEFAULT 'aurora',
    calm          INTEGER NOT NULL DEFAULT 0,
    autoplay      INTEGER NOT NULL DEFAULT 1,
    dm_policy     TEXT    NOT NULL DEFAULT 'everyone',
    accepts_offers INTEGER NOT NULL DEFAULT 1,
    activity_seen TEXT,
    status        TEXT    NOT NULL DEFAULT 'online',
    is_bot        INTEGER NOT NULL DEFAULT 0,
    college_email TEXT,
    verified_at   TEXT,
    created_at    TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users (username);

CREATE TABLE IF NOT EXISTS projects (
    id             SERIAL PRIMARY KEY,
    innovator_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title          TEXT    NOT NULL,
    summary        TEXT    NOT NULL,
    description    TEXT    NOT NULL,
    domain         TEXT    NOT NULL,
    stage          TEXT    NOT NULL
                   CHECK (stage IN ('idea', 'prototype', 'mvp', 'growth')),
    funding_needed INTEGER NOT NULL CHECK (funding_needed > 0),
    media_path     TEXT,
    media_type     TEXT    CHECK (media_type IN ('image', 'video')),
    accepts_money  INTEGER NOT NULL DEFAULT 1,
    accepts_goods  INTEGER NOT NULL DEFAULT 0,
    accepts_pilot  INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE INDEX IF NOT EXISTS idx_projects_innovator ON projects (innovator_id);

CREATE TABLE IF NOT EXISTS sponsor_profiles (
    user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    organisation TEXT    NOT NULL,
    bio          TEXT    NOT NULL,
    budget_min   INTEGER NOT NULL CHECK (budget_min > 0),
    budget_max   INTEGER NOT NULL,
    updated_at   TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')),
    CHECK (budget_max >= budget_min)
);

CREATE TABLE IF NOT EXISTS sponsor_domains (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    domain  TEXT    NOT NULL,
    PRIMARY KEY (user_id, domain)
);

CREATE TABLE IF NOT EXISTS interests (
    id          SERIAL PRIMARY KEY,
    sponsor_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status      TEXT    NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'accepted', 'declined', 'withdrawn')),
    amount      INTEGER,
    goods       TEXT    NOT NULL DEFAULT '',
    pilot       INTEGER NOT NULL DEFAULT 0,
    turn        TEXT    NOT NULL DEFAULT 'creator',
    last_actor  INTEGER,
    message     TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')),
    responded_at TEXT,
    UNIQUE (sponsor_id, project_id)
);

CREATE INDEX IF NOT EXISTS idx_interests_project ON interests (project_id);

CREATE TABLE IF NOT EXISTS project_members (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at  TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')),
    PRIMARY KEY (project_id, user_id)
);

CREATE TABLE IF NOT EXISTS project_invites (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    code       TEXT    NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS channels (
    id         SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    team_only  INTEGER NOT NULL DEFAULT 0 CHECK (team_only IN (0, 1)),
    created_at TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')),
    UNIQUE (project_id, name)
);

CREATE TABLE IF NOT EXISTS messages (
    id         SERIAL PRIMARY KEY,
    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    sender_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body       TEXT    NOT NULL CHECK (length(body) BETWEEN 1 AND 2000),
    created_at TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages (channel_id, id);

CREATE TABLE IF NOT EXISTS dm_threads (
    id           SERIAL PRIMARY KEY,
    user_a       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_b       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    requested_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status       TEXT    NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'accepted', 'declined')),
    created_at   TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')),
    CHECK (user_a < user_b),
    UNIQUE (user_a, user_b)
);

CREATE TABLE IF NOT EXISTS dm_messages (
    id         SERIAL PRIMARY KEY,
    thread_id  INTEGER NOT NULL REFERENCES dm_threads(id) ON DELETE CASCADE,
    sender_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body       TEXT    NOT NULL DEFAULT '' CHECK (length(body) <= 2000),
    attachment TEXT,
    attach_type TEXT CHECK (attach_type IN ('image', 'video', 'audio', 'file')),
    attach_name TEXT,
    reply_to INTEGER REFERENCES dm_messages(id) ON DELETE SET NULL,
    edited INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')),
    CHECK (length(body) > 0 OR attachment IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_dm_messages_thread ON dm_messages (thread_id, id);

CREATE TABLE IF NOT EXISTS likes (
    post_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')),
    PRIMARY KEY (post_id, user_id)
);

CREATE TABLE IF NOT EXISTS comments (
    id         SERIAL PRIMARY KEY,
    post_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body       TEXT    NOT NULL CHECK (length(body) BETWEEN 1 AND 500),
    created_at TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE INDEX IF NOT EXISTS idx_comments_post ON comments (post_id, id);

-- ---------- Servers (Discord-style) ----------

CREATE TABLE IF NOT EXISTS servers (
    id          SERIAL PRIMARY KEY,
    owner_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
    icon        TEXT    NOT NULL DEFAULT '',
    avatar      TEXT,
    color       TEXT    NOT NULL DEFAULT '#5865f2',
    description TEXT    NOT NULL DEFAULT '',
    invite_code TEXT    NOT NULL UNIQUE,
    is_public   INTEGER NOT NULL DEFAULT 1 CHECK (is_public IN (0, 1)),
    rules       TEXT    NOT NULL DEFAULT '',
    welcome     TEXT    NOT NULL DEFAULT '',
    base_perms  INTEGER NOT NULL DEFAULT 15,
    goal_title  TEXT    NOT NULL DEFAULT '',
    goal_amount INTEGER NOT NULL DEFAULT 0,
    project_id  INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    created_at  TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_servers_project ON servers (project_id) WHERE project_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS server_roles (
    id        SERIAL PRIMARY KEY,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    name      TEXT    NOT NULL,
    color     TEXT    NOT NULL DEFAULT '#99aab5',
    is_admin  INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
    position  INTEGER NOT NULL DEFAULT 0,
    perms     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (server_id, name)
);

CREATE TABLE IF NOT EXISTS server_members (
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')),
    nickname  TEXT,
    timeout_until TEXT,
    accepted_rules INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (server_id, user_id)
);

CREATE TABLE IF NOT EXISTS member_roles (
    server_id INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    role_id   INTEGER NOT NULL REFERENCES server_roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id),
    FOREIGN KEY (server_id, user_id) REFERENCES server_members(server_id, user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS server_categories (
    id        SERIAL PRIMARY KEY,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    name      TEXT    NOT NULL,
    position  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS server_channels (
    id          SERIAL PRIMARY KEY,
    server_id   INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    category_id INTEGER REFERENCES server_categories(id) ON DELETE SET NULL,
    name        TEXT    NOT NULL,
    kind        TEXT    NOT NULL DEFAULT 'text' CHECK (kind IN ('text', 'announcement', 'voice', 'forum', 'thread')),
    topic       TEXT    NOT NULL DEFAULT '',
    is_private  INTEGER NOT NULL DEFAULT 0 CHECK (is_private IN (0, 1)),
    position    INTEGER NOT NULL DEFAULT 0,
    slowmode    INTEGER NOT NULL DEFAULT 0,
    read_only   INTEGER NOT NULL DEFAULT 0,
    parent_id   INTEGER REFERENCES server_channels(id) ON DELETE CASCADE,
    parent_message_id INTEGER,
    created_by  INTEGER
);

CREATE TABLE IF NOT EXISTS channel_roles (
    channel_id INTEGER NOT NULL REFERENCES server_channels(id) ON DELETE CASCADE,
    role_id    INTEGER NOT NULL REFERENCES server_roles(id) ON DELETE CASCADE,
    PRIMARY KEY (channel_id, role_id)
);

CREATE TABLE IF NOT EXISTS server_messages (
    id         SERIAL PRIMARY KEY,
    channel_id INTEGER NOT NULL REFERENCES server_channels(id) ON DELETE CASCADE,
    sender_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body       TEXT    NOT NULL DEFAULT '' CHECK (length(body) <= 2000),
    attachment TEXT,
    attach_type TEXT CHECK (attach_type IN ('image', 'video', 'audio', 'file')),
    attach_name TEXT,
    reply_to INTEGER REFERENCES server_messages(id) ON DELETE SET NULL,
    edited INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0,
    embed TEXT,
    created_at TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')),
    CHECK (length(body) > 0 OR attachment IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_server_messages_channel ON server_messages (channel_id, id);

CREATE TABLE IF NOT EXISTS post_media (
    id       SERIAL PRIMARY KEY,
    post_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path     TEXT    NOT NULL,
    type     TEXT    NOT NULL CHECK (type IN ('image', 'video')),
    position INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_post_media_post ON post_media (post_id, position);

-- Notifications: how far each person has read in each chat and channel.
CREATE TABLE IF NOT EXISTS dm_reads (
    thread_id INTEGER NOT NULL REFERENCES dm_threads(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    last_id   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (thread_id, user_id)
);

CREATE TABLE IF NOT EXISTS channel_reads (
    channel_id INTEGER NOT NULL REFERENCES server_channels(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    last_id    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (channel_id, user_id)
);

-- Muted chats (kind 'dm' = thread id), channels and servers: no pop-ups, no badges.
CREATE TABLE IF NOT EXISTS mutes (
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind      TEXT    NOT NULL CHECK (kind IN ('dm', 'channel', 'server')),
    target_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, kind, target_id)
);

-- ---------- Servers, round two: permissions, moderation, companions (bots), extras ----------

CREATE TABLE IF NOT EXISTS server_bans (
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason    TEXT    NOT NULL DEFAULT '',
    banned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT   NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')),
    PRIMARY KEY (server_id, user_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id        SERIAL PRIMARY KEY,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    actor_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action    TEXT    NOT NULL,
    detail    TEXT    NOT NULL DEFAULT '',
    created_at TEXT   NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE INDEX IF NOT EXISTS idx_audit_server ON audit_log (server_id, id);

CREATE TABLE IF NOT EXISTS message_reactions (
    message_id INTEGER NOT NULL REFERENCES server_messages(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    emoji      TEXT    NOT NULL,
    PRIMARY KEY (message_id, user_id, emoji)
);

CREATE TABLE IF NOT EXISTS poll_votes (
    message_id INTEGER NOT NULL REFERENCES server_messages(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    choice     INTEGER NOT NULL,
    PRIMARY KEY (message_id, user_id)
);

-- A companion is a bot. Built-in ones have a kind like 'dice'; your own have kind 'custom'
-- (talk to us over the API) or 'webhook' (post into one channel with a secret URL).
CREATE TABLE IF NOT EXISTS bots (
    id          SERIAL PRIMARY KEY,
    server_id   INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    token_hash  TEXT,
    interaction_url TEXT NOT NULL DEFAULT '',
    channel_id  INTEGER REFERENCES server_channels(id) ON DELETE CASCADE,
    config      TEXT    NOT NULL DEFAULT '{}',
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE INDEX IF NOT EXISTS idx_bots_server ON bots (server_id);

CREATE TABLE IF NOT EXISTS bot_commands (
    bot_id      INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (bot_id, name)
);

CREATE TABLE IF NOT EXISTS member_xp (
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    xp        INTEGER NOT NULL DEFAULT 0,
    last_at   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (server_id, user_id)
);

CREATE TABLE IF NOT EXISTS reminders (
    id        SERIAL PRIMARY KEY,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    channel_id INTEGER NOT NULL REFERENCES server_channels(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    due_at    INTEGER NOT NULL,
    text      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS server_invites (
    code       TEXT PRIMARY KEY,
    server_id  INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    max_uses   INTEGER NOT NULL DEFAULT 0,
    uses       INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'))
);

CREATE TABLE IF NOT EXISTS server_emojis (
    id        SERIAL PRIMARY KEY,
    server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    name      TEXT    NOT NULL,
    path      TEXT    NOT NULL,
    UNIQUE (server_id, name)
);

CREATE TABLE IF NOT EXISTS server_events (
    id          SERIAL PRIMARY KEY,
    server_id   INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    creator_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    starts_at   TEXT    NOT NULL,
    channel_id  INTEGER REFERENCES server_channels(id) ON DELETE SET NULL,
    notified    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS event_rsvps (
    event_id INTEGER NOT NULL REFERENCES server_events(id) ON DELETE CASCADE,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, user_id)
);

-- ---------- Project servers: updates, milestones, followers ----------

CREATE TABLE IF NOT EXISTS project_updates (
    id         SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    author_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body       TEXT    NOT NULL CHECK (length(body) BETWEEN 1 AND 500),
    image      TEXT,
    created_at TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE INDEX IF NOT EXISTS idx_project_updates ON project_updates (project_id, id);

CREATE TABLE IF NOT EXISTS project_milestones (
    id         SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title      TEXT    NOT NULL,
    amount     INTEGER NOT NULL CHECK (amount > 0),
    done       INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0, 1)),
    announced  INTEGER NOT NULL DEFAULT 0 CHECK (announced IN (0, 1)),
    proof       TEXT,
    proof_image TEXT,
    proof_at    TEXT,
    released_at TEXT,
    sent_back   TEXT,
    due_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_milestones_project ON project_milestones (project_id);

CREATE TABLE IF NOT EXISTS project_follows (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (project_id, user_id)
);

-- Messages for a project's server that could not be delivered yet (no server, or Sponsor Scout has no channel).
-- They are sent in order the moment the server and its Scout are ready.
CREATE TABLE IF NOT EXISTS pending_posts (
    id         SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    body       TEXT    NOT NULL DEFAULT '',
    embed      TEXT,
    created_at TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE INDEX IF NOT EXISTS idx_pending_posts ON pending_posts (project_id, id);

-- Every step of a sponsorship deal: the offer, each counter, the final agreement. Terms are a snapshot.
CREATE TABLE IF NOT EXISTS deal_rounds (
    id          SERIAL PRIMARY KEY,
    interest_id INTEGER NOT NULL REFERENCES interests(id) ON DELETE CASCADE,
    actor_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL,
    amount      INTEGER,
    goods       TEXT    NOT NULL DEFAULT '',
    pilot       INTEGER NOT NULL DEFAULT 0,
    summary     TEXT    NOT NULL DEFAULT '',
    note        TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE INDEX IF NOT EXISTS idx_deal_rounds ON deal_rounds (interest_id, id);

-- One side's private chat with the counter-offer coach, for one deal. Survives closing/reopening the
-- panel and carries across negotiation rounds; each party has their own, independent thread.
CREATE TABLE IF NOT EXISTS deal_coach_messages (
    id          SERIAL PRIMARY KEY,
    interest_id INTEGER NOT NULL REFERENCES interests(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT    NOT NULL CHECK (role IN ('user', 'model')),
    body        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE INDEX IF NOT EXISTS idx_deal_coach_messages ON deal_coach_messages (interest_id, user_id, id);

-- ---------- Nova: the platform's AI assistant ----------

CREATE TABLE IF NOT EXISTS ai_messages (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       TEXT    NOT NULL CHECK (role IN ('user', 'model')),
    body       TEXT    NOT NULL,
    attachment TEXT,
    attach_type TEXT,
    attach_name TEXT,
    created_at TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE INDEX IF NOT EXISTS idx_ai_messages ON ai_messages (user_id, id);

-- Durable facts Nova has learned about one person, carried into every future conversation.
CREATE TABLE IF NOT EXISTS ai_memory (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    fact       TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE INDEX IF NOT EXISTS idx_ai_memory ON ai_memory (user_id, id);

-- Anything Nova wants to do on a person's behalf (send a DM, post an update) waits here for one click.
CREATE TABLE IF NOT EXISTS ai_actions (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL CHECK (kind IN ('send_dm', 'post_update')),
    payload    TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'))
);
CREATE INDEX IF NOT EXISTS idx_ai_actions ON ai_actions (user_id, id);

-- ---------- Milestone escrow (simulated) and verification ----------

-- Sponsors vote on the proof a creator uploads for a funded milestone. Votes count by money given.
CREATE TABLE IF NOT EXISTS milestone_votes (
    milestone_id INTEGER NOT NULL REFERENCES project_milestones(id) ON DELETE CASCADE,
    sponsor_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    vote         TEXT    NOT NULL CHECK (vote IN ('approve', 'reject')),
    note         TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')),
    PRIMARY KEY (milestone_id, sponsor_id)
);

-- The one code waiting to prove someone owns a college email address.
CREATE TABLE IF NOT EXISTS email_codes (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    email      TEXT    NOT NULL,
    code_hash  TEXT    NOT NULL,
    tries      INTEGER NOT NULL DEFAULT 0,
    sent_at    TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')),
    expires_at TEXT    NOT NULL
);

-- The one reset link waiting to be used for an account (a password forgotten, not a college email).
CREATE TABLE IF NOT EXISTS password_resets (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT    NOT NULL UNIQUE,
    sent_at    TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')),
    expires_at TEXT    NOT NULL
);

-- A faculty mentor vouches for a project by clicking a link sent to their college email.
CREATE TABLE IF NOT EXISTS project_endorsements (
    id           SERIAL PRIMARY KEY,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    mentor_name  TEXT    NOT NULL,
    mentor_email TEXT    NOT NULL,
    token_hash   TEXT    NOT NULL UNIQUE,
    status       TEXT    NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'endorsed')),
    created_at   TEXT    NOT NULL DEFAULT (to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')),
    endorsed_at  TEXT,
    UNIQUE (project_id, mentor_email)
);
