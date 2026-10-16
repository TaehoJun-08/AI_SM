"""Companions: the bots of a server.

Some come built in (Herald, Dicey, Pollster, Nudge, Sparks, Scout, Guard). Others are yours:
a "custom" companion talks to us over the HTTP API (servers/botapi.py) and a "webhook" is a secret URL that
posts into one channel. Every companion is a real (login-less) user row with is_bot = 1, so its
messages, name and place in the member list work like anybody else's."""
import hashlib
import json
import math
import random
import re
import secrets
import time
import urllib.request
from types import SimpleNamespace
from flask import current_app
from werkzeug.security import generate_password_hash
from extensions import socketio
import live
from servers.messages import post_message, tell_only

SLUG = re.compile(r"[^A-Za-z0-9_]")
DURATION = re.compile(r"(\d{1,4})\s*([smhd])", re.I)

# ---------- the built-in catalogue ----------
# config fields: (key, label, kind 'text' | 'long' | 'check' | 'channel', default, help)
CATALOG = {
    "herald": {
        "name": "Herald", "emoji": "📯", "color": "#f59e0b",
        "blurb": "Greets every new member in a channel of your choice and answers /rules.",
        "commands": {"rules": ("Show the server rules", ""), "welcome": ("Preview the welcome message (managers)", "")},
        "config": [("channel", "Welcome channel", "channel", "", "Leave empty to use #general"),
                   ("message", "Welcome message", "long", "Welcome to {server}, {user}! 🎉 You are member #{count}.",
                    "Use {user}, {server} and {count}")],
    },
    "dicey": {
        "name": "Dicey", "emoji": "🎲", "color": "#10b981",
        "blurb": "Dice, coin flips, a magic 8-ball and a decision maker for your team.",
        "commands": {"roll": ("Roll dice, e.g. 2d6 (default 1d20)", "[NdM]"), "coin": ("Flip a coin", ""),
                     "8ball": ("Ask the magic 8-ball", "<question>"),
                     "choose": ("Pick one option for you", "<a> | <b> | <c>")},
        "config": [],
    },
    "pollster": {
        "name": "Pollster", "emoji": "📊", "color": "#6366f1",
        "blurb": "Quick polls with live bars. Everyone votes with one click.",
        "commands": {"poll": ("Start a poll (2-8 options)", "<question> | <option> | <option>")},
        "config": [],
    },
    "nudge": {
        "name": "Nudge", "emoji": "⏰", "color": "#ec4899",
        "blurb": "Reminders that ping you in the same channel when the time comes.",
        "commands": {"remind": ("Remind me later, e.g. /remind 20m call Maya", "<time> <what>"),
                     "reminders": ("List my reminders", "")},
        "config": [],
    },
    "sparks": {
        "name": "Sparks", "emoji": "✨", "color": "#8b5cf6",
        "blurb": "Members earn sparks for chatting and level up. Leaderboard included.",
        "commands": {"rank": ("Show your level (or someone else's)", "[@user]"), "top": ("Top 10 members", "")},
        "config": [("announce", "Announce level-ups in the channel", "check", "1", "")],
    },
    "scout": {
        "name": "Sponsor Scout", "emoji": "🔭", "color": "#0ea5e9",
        "blurb": "Made for AI Sponsor: shares project cards, tracks the community goal and cheers when a member is sponsored.",
        "commands": {"status": ("Show this project server's status", ""),
                     "pitch": ("Share one of your projects here", "[post id]"),
                     "funding": ("Community goal and top-funded projects", "")},
        "config": [("channel", "Where to cheer for new sponsors", "channel", "", "Leave empty to use #announcements")],
    },
    "guard": {
        "name": "Guard", "emoji": "🛡️", "color": "#ef4444",
        "blurb": "Auto-moderation: blocks chosen words, link spam and mention floods. Managers are exempt.",
        "commands": {},
        "config": [("words", "Blocked words (comma separated)", "long", "", ""),
                   ("links", "Block links from regular members", "check", "0", ""),
                   ("mentions", "Max @mentions in one message (0 = no limit)", "text", "5", "")],
    },
}
CORE_COMMANDS = {"help": ("List every command here", ""), "nick": ("Change your nickname in this server", "<name>"),
                 "shrug": ("Add ¯\\_(ツ)_/¯ to your message", "[text]"),
                 "spoiler": ("Send your text hidden behind a spoiler", "<text>")}
EIGHT_BALL = ["Yes, definitely.", "It is certain.", "Most likely.", "Ask again later.", "Cannot predict now.",
              "Don't count on it.", "My sources say no.", "Very doubtful.", "Signs point to yes.", "Outlook good."]


# ---------- installing and finding companions ----------

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def make_bot_user(db, label, server_id):
    base = SLUG.sub("", label)[:12] or "bot"
    n = 1
    while db.execute("SELECT 1 FROM users WHERE username = ?", (f"{base}_{server_id}_{n}",)).fetchone():
        n += 1
    uname = f"{base}_{server_id}_{n}"
    return db.execute(
        """INSERT INTO users (full_name, username, email, password_hash, bio, is_bot)
           VALUES (?, ?, ?, ?, ?, 1)""",
        (label, uname, f"{uname}@bots.invalid", generate_password_hash(secrets.token_hex(16)), "A companion")).lastrowid


def install(db, server_id, kind, created_by, name=None, channel_id=None, url=""):
    """Add a companion to a server. Returns (bot id, token or None)."""
    label = (name or CATALOG.get(kind, {}).get("name") or "Companion").strip()[:30]
    user_id = make_bot_user(db, label, server_id)
    db.execute("INSERT INTO server_members (server_id, user_id) VALUES (?, ?)", (server_id, user_id))
    token = secrets.token_urlsafe(32) if kind in ("custom", "webhook") else None
    config = {k: d for k, _, _, d, _ in CATALOG[kind]["config"]} if kind in CATALOG else {}
    bot_id = db.execute(
        """INSERT INTO bots (server_id, user_id, kind, name, token_hash, interaction_url, channel_id, config, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (server_id, user_id, kind, label, token_hash(token) if token else None, url, channel_id,
         json.dumps(config), created_by)).lastrowid
    db.commit()
    return bot_id, token


def remove(db, bot_id, server_id):
    row = db.execute("SELECT user_id, kind FROM bots WHERE id = ? AND server_id = ?", (bot_id, server_id)).fetchone()
    if row:
        if row["kind"] == "nudge":
            db.execute("DELETE FROM reminders WHERE server_id = ?", (server_id,))
        db.execute("DELETE FROM users WHERE id = ?", (row["user_id"],))   # cascades to the bots row and membership
        db.commit()
    return row


def bots_of(db, server_id, kind=None):
    rows = db.execute("SELECT * FROM bots WHERE server_id = ? ORDER BY id", (server_id,)).fetchall()
    return [dict(r, cfg=json.loads(r["config"] or "{}")) for r in rows if kind is None or r["kind"] == kind]


def works_in(bot, channel):
    """A companion with a channel list only works there (threads count as their parent); no list = everywhere."""
    only = bot["cfg"].get("only") or []
    return not only or channel["id"] in only or (channel["parent_id"] or 0) in only


def settings_data(db, server_id):
    installed = bots_of(db, server_id)
    have = {b["kind"] for b in installed}
    commands = {}
    for r in db.execute("SELECT bot_id, name, description FROM bot_commands WHERE bot_id IN (SELECT id FROM bots WHERE server_id = ?)", (server_id,)):
        commands.setdefault(r["bot_id"], []).append(r)
    return {"installed": installed, "catalog": CATALOG, "available": [k for k in CATALOG if k not in have],
            "bot_commands": commands,
            "text_channels": db.execute("SELECT id, name FROM server_channels WHERE server_id = ? AND kind IN ('text','announcement') ORDER BY position, id", (server_id,)).fetchall()}


def command_list(db, server_id):
    """Everything someone can type after a slash in this server (for the autocomplete)."""
    out = [{"name": n, "description": d, "usage": u, "bot": "Server"} for n, (d, u) in CORE_COMMANDS.items()]
    for b in bots_of(db, server_id):
        if b["kind"] in CATALOG:
            out += [{"name": n, "description": d, "usage": u, "bot": b["name"]} for n, (d, u) in CATALOG[b["kind"]]["commands"].items()]
        elif b["kind"] == "custom":
            out += [{"name": r["name"], "description": r["description"], "usage": "", "bot": b["name"]}
                    for r in db.execute("SELECT * FROM bot_commands WHERE bot_id = ?", (b["id"],))]
    return sorted(out, key=lambda c: c["name"])


def first_text_channel(db, server_id, name=None):
    row = None
    if name:
        row = db.execute("SELECT * FROM server_channels WHERE server_id = ? AND name = ? AND kind IN ('text','announcement')", (server_id, name)).fetchone()
    return row or db.execute("""SELECT * FROM server_channels WHERE server_id = ? AND is_private = 0 AND kind IN ('text','announcement')
                                ORDER BY (name = 'general') DESC, position, id LIMIT 1""", (server_id,)).fetchone()


def channel_from_config(db, server_id, cfg, fallback="general"):
    value = (cfg.get("channel") or "").strip()
    row = db.execute("SELECT * FROM server_channels WHERE id = ? AND server_id = ?", (value, server_id)).fetchone() if value.isdigit() else None
    return row or first_text_channel(db, server_id, fallback)


# ---------- hooks the rest of the app calls ----------

def on_join(db, server_id, user_id):
    for bot in bots_of(db, server_id, "herald"):
        channel = channel_from_config(db, server_id, bot["cfg"])
        if channel is None:
            continue
        server = db.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
        count = db.execute("SELECT COUNT(*) FROM server_members WHERE server_id = ?", (server_id,)).fetchone()[0]
        text = (server["welcome"] or bot["cfg"].get("message") or CATALOG["herald"]["config"][1][3])
        text = text.replace("{user}", f"@{live.username(db, user_id)}").replace("{server}", server["name"]).replace("{count}", str(count))
        post_message(db, channel, bot["user_id"], text, silent=False)


def screen(db, server_id, m, user_id, body, channel=None):
    """Guard: a reason to refuse this message, or None. Managers are exempt."""
    if "manage_messages" in m["perms"]:
        return None
    for bot in bots_of(db, server_id, "guard"):
        if channel is not None and not works_in(bot, channel):
            continue
        cfg = bot["cfg"]
        low = body.lower()
        for word in re.split(r"[,\n]", cfg.get("words", "")):
            word = word.strip().lower()
            if word and re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", low):
                return "that word isn't allowed here"
        if cfg.get("links") in ("1", 1, True) and re.search(r"https?://|www\.", low):
            return "links aren't allowed for members here"
        limit = int(cfg.get("mentions") or 0) if str(cfg.get("mentions") or "0").isdigit() else 0
        if limit and len(re.findall(r"@\w", body)) > limit:
            return f"too many mentions (max {limit})"
    return None


def on_message(db, server, channel, user_id):
    """Sparks: a little XP for talking, at most once every 45 seconds."""
    for bot in bots_of(db, server["id"], "sparks"):
        if not works_in(bot, channel):
            continue
        now = int(time.time())
        row = db.execute("SELECT * FROM member_xp WHERE server_id = ? AND user_id = ?", (server["id"], user_id)).fetchone()
        if row and now - row["last_at"] < 45:
            return
        gain = random.randint(8, 15)
        before = row["xp"] if row else 0
        db.execute("""INSERT INTO member_xp (server_id, user_id, xp, last_at) VALUES (?, ?, ?, ?)
                      ON CONFLICT(server_id, user_id) DO UPDATE SET xp = xp + ?, last_at = ?""",
                   (server["id"], user_id, gain, now, gain, now))
        db.commit()
        if level_of(before + gain) > level_of(before) and bot["cfg"].get("announce") in ("1", 1, True):
            post_message(db, channel, bot["user_id"], f"✨ @{live.username(db, user_id)} reached **level {level_of(before + gain)}**!", silent=True)


def on_sponsorship(db, project_id, sponsor_id, terms):
    """Scout cheers in the server of THIS project only (queued until that server exists). The card carries the
    money raised so far (not just this offer), so it always shows the current total, not a stale snapshot."""
    project = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if project is None:
        return
    from servers import projectlink
    projectlink.on_sponsorship(db, project_id, sponsor_id)
    import deals
    who, owner = live.username(db, sponsor_id), live.username(db, project["innovator_id"])
    if terms["money"] or terms["goods"]:
        title = f"💸 @{who} just backed “{project['title']}” by @{owner} with {deals.describe(terms)}!"
    else:
        title = f"🌱 @{who} is now a free sponsor of “{project['title']}” by @{owner}. They'll use it and share feedback!"
    embed = {"kind": "project", "title": title, "color": "#10b981", "url": f"/p/{project_id}"}
    if project["accepts_money"]:
        raised = projectlink.raised(db, project_id)
        embed["progress"] = {"value": raised, "goal": project["funding_needed"],
                             "label": f"₹{raised:,} of ₹{project['funding_needed']:,} sponsored"}
    projectlink.project_post(db, project_id, embed=embed)


def level_of(xp):
    return int(math.sqrt(xp / 40))


# ---------- slash commands ----------

def run_command(db, server, m, channel, user_id, text):
    """Handle a message that starts with "/". Returns None if it isn't a command we know (so it is sent as
    normal text), ("done",) if handled, or ("rewrite", new text) to send the person's own changed message."""
    name, _, rest = text[1:].partition(" ")
    name, rest = name.lower(), rest.strip()
    if not name:
        return None
    ctx = SimpleNamespace(db=db, server=server, m=m, channel=channel, user_id=user_id, args=rest, bot=None,
                          username=live.username(db, user_id))
    if name == "help":
        lines = [f"**/{c['name']}** {c['usage']} · {c['description']}  _({c['bot']})_" for c in command_list(db, server["id"])]
        tell_only(user_id, channel["id"], "Commands you can use here:\n" + "\n".join(lines))
        return ("done",)
    if name == "nick":
        nick = " ".join(rest.split())[:32] or None
        db.execute("UPDATE server_members SET nickname = ? WHERE server_id = ? AND user_id = ?", (nick, server["id"], user_id))
        db.commit()
        live.server_changed(server["id"])
        tell_only(user_id, channel["id"], f"Your nickname is now {nick}." if nick else "Nickname cleared.")
        return ("done",)
    if name == "shrug":
        return ("rewrite", (rest + " ¯\\_(ツ)_/¯").strip())
    if name == "spoiler" and rest:
        return ("rewrite", f"||{rest}||")
    blocked = None
    for bot in bots_of(db, server["id"]):
        mine = (bot["kind"] in CATALOG and name in CATALOG[bot["kind"]]["commands"]) or (
            bot["kind"] == "custom" and db.execute("SELECT 1 FROM bot_commands WHERE bot_id = ? AND name = ?", (bot["id"], name)).fetchone())
        if mine and not works_in(bot, channel):
            blocked = blocked or bot
            continue
        if bot["kind"] in CATALOG and name in CATALOG[bot["kind"]]["commands"]:
            ctx.bot = bot
            HANDLERS[(bot["kind"], name)](ctx)
            return ("done",)
        if bot["kind"] == "custom" and db.execute("SELECT 1 FROM bot_commands WHERE bot_id = ? AND name = ?", (bot["id"], name)).fetchone():
            send_to_bot(db, bot, server, channel, user_id, ctx.username, name, rest)
            return ("done",)
    if blocked:
        tell_only(user_id, channel["id"], f"{blocked['name']} isn't allowed to work in this channel.")
        return ("done",)
    return None


def say(ctx, content="", embed=None):
    return post_message(ctx.db, ctx.channel, ctx.bot["user_id"], content, embed=embed)


def whisper(ctx, text):
    tell_only(ctx.user_id, ctx.channel["id"], text)


def find_user(ctx, text):
    name = text.strip().lstrip("@")
    row = ctx.db.execute("""SELECT u.id, u.username FROM users u JOIN server_members sm ON sm.user_id = u.id
                            WHERE sm.server_id = ? AND u.username = ?""", (ctx.server["id"], name)).fetchone()
    return row


# ----- Herald -----
def cmd_rules(ctx):
    rules = ctx.server["rules"].strip()
    say(ctx, embed={"title": f"📜 Rules of {ctx.server['name']}", "description": rules or "No rules written yet.", "color": "#f59e0b"})


def cmd_welcome(ctx):
    if "manage_server" not in ctx.m["perms"]:
        return whisper(ctx, "Only people who can manage the server can do that.")
    on_join(ctx.db, ctx.server["id"], ctx.user_id)


# ----- Dicey -----
def cmd_roll(ctx):
    found = re.fullmatch(r"(\d{1,3})?d(\d{1,4})", ctx.args.lower().replace(" ", "") or "1d20")
    if not found:
        return whisper(ctx, "Try /roll 2d6 (dice d sides).")
    n, sides = int(found.group(1) or 1), int(found.group(2))
    if n < 1 or n > 100 or sides < 2:
        return whisper(ctx, "Roll between 1 and 100 dice with at least 2 sides.")
    rolls = [random.randint(1, sides) for _ in range(n)]
    shown = " + ".join(map(str, rolls)) if n <= 20 else f"{n} dice"
    say(ctx, f"🎲 @{ctx.username} rolled {n}d{sides}: {shown} = **{sum(rolls)}**")


def cmd_coin(ctx):
    say(ctx, f"🪙 @{ctx.username} flipped a coin: **{random.choice(['Heads', 'Tails'])}**")


def cmd_8ball(ctx):
    if not ctx.args:
        return whisper(ctx, "Ask a question: /8ball will we win the pitch?")
    say(ctx, embed={"title": "🎱 " + ctx.args[:200], "description": random.choice(EIGHT_BALL), "color": "#111827",
                    "footer": f"asked by @{ctx.username}"})


def cmd_choose(ctx):
    options = [o.strip() for o in ctx.args.split("|") if o.strip()]
    if len(options) < 2:
        return whisper(ctx, "Give me at least two options: /choose pizza | biryani | tacos")
    say(ctx, f"🤔 I choose **{random.choice(options)}**  _(from {len(options)} options)_")


# ----- Pollster -----
def cmd_poll(ctx):
    parts = [p.strip() for p in ctx.args.split("|") if p.strip()]
    if len(parts) < 3 or len(parts) > 9:
        return whisper(ctx, "Use: /poll Question | option 1 | option 2  (up to 8 options)")
    say(ctx, embed={"kind": "poll", "title": parts[0][:200], "poll": parts[1:], "color": "#6366f1",
                    "footer": f"Poll by @{ctx.username} · click an option to vote"})


# ----- Nudge -----
def cmd_remind(ctx):
    found = re.match(r"((?:\d{1,4}\s*[smhd]\s*)+)(.*)", ctx.args, re.I)
    if not found or not found.group(2).strip():
        return whisper(ctx, "Use: /remind 20m call Maya   (units: s, m, h, d)")
    seconds = sum(int(n) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[u.lower()] for n, u in DURATION.findall(found.group(1)))
    if not 5 <= seconds <= 7 * 86400:
        return whisper(ctx, "Pick a time between 5 seconds and 7 days.")
    ctx.db.execute("INSERT INTO reminders (server_id, channel_id, user_id, due_at, text) VALUES (?, ?, ?, ?, ?)",
                   (ctx.server["id"], ctx.channel["id"], ctx.user_id, int(time.time()) + seconds, found.group(2).strip()[:300]))
    ctx.db.commit()
    whisper(ctx, f"⏰ I'll remind you in {nice_duration(seconds)}.")


def cmd_reminders(ctx):
    rows = ctx.db.execute("SELECT * FROM reminders WHERE server_id = ? AND user_id = ? ORDER BY due_at",
                          (ctx.server["id"], ctx.user_id)).fetchall()
    whisper(ctx, "\n".join(f"⏰ in {nice_duration(r['due_at'] - int(time.time()))}: {r['text']}" for r in rows) or "You have no reminders.")


def nice_duration(seconds):
    seconds = max(int(seconds), 0)
    for size, word in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            n = seconds // size
            return f"{n} {word}{'s' if n != 1 else ''}"
    return f"{seconds} seconds"


# ----- Sparks -----
def rank_embed(ctx, user_id, name):
    row = ctx.db.execute("SELECT xp FROM member_xp WHERE server_id = ? AND user_id = ?", (ctx.server["id"], user_id)).fetchone()
    xp = row["xp"] if row else 0
    lvl = level_of(xp)
    floor_xp, ceil_xp = 40 * lvl ** 2, 40 * (lvl + 1) ** 2
    return {"title": f"✨ {name} · level {lvl}", "color": "#8b5cf6", "progress": {"value": xp - floor_xp, "goal": ceil_xp - floor_xp,
            "label": f"{xp} sparks · {ceil_xp - xp} to level {lvl + 1}"}}


def cmd_rank(ctx):
    if ctx.args:
        target = find_user(ctx, ctx.args)
        if target is None:
            return whisper(ctx, "I can't find that member.")
        tid, tname = target["id"], target["username"]
    else:
        tid, tname = ctx.user_id, ctx.username
    say(ctx, embed=rank_embed(ctx, tid, "@" + tname))


def cmd_top(ctx):
    rows = ctx.db.execute("""SELECT u.username, x.xp FROM member_xp x JOIN users u ON u.id = x.user_id
                             WHERE x.server_id = ? ORDER BY x.xp DESC LIMIT 10""", (ctx.server["id"],)).fetchall()
    medals = ["🥇", "🥈", "🥉"]
    text = "\n".join(f"{medals[i] if i < 3 else str(i + 1) + '.'} @{r['username']} · level {level_of(r['xp'])} · {r['xp']} sparks" for i, r in enumerate(rows))
    say(ctx, embed={"title": "🏆 Top members", "description": text or "Nobody has sparks yet. Start chatting!", "color": "#8b5cf6"})


# ----- Sponsor Scout -----
def raised_for(db, project_id):
    return db.execute("SELECT COALESCE(SUM(amount), 0) FROM interests WHERE project_id = ? AND status = 'accepted'", (project_id,)).fetchone()[0]


def cmd_pitch(ctx):
    db = ctx.db
    if ctx.args.strip().isdigit():
        project = db.execute("SELECT * FROM projects WHERE id = ? AND innovator_id = ?", (int(ctx.args), ctx.user_id)).fetchone()
    else:
        project = db.execute("SELECT * FROM projects WHERE innovator_id = ? ORDER BY id DESC LIMIT 1", (ctx.user_id,)).fetchone()
    if project is None:
        return whisper(ctx, "I couldn't find a project of yours. Post one on the feed first, or use /pitch <post id>.")
    raised = raised_for(db, project["id"])
    import deals
    wants = " · ".join(deals.LABEL[k] for k in deals.kinds_of(project))
    embed = {"kind": "project", "title": project["title"], "description": project["summary"], "color": "#0ea5e9",
             "url": f"/p/{project['id']}",
             "fields": [{"name": "Domain", "value": project["domain"], "inline": True},
                        {"name": "Stage", "value": project["stage"].title(), "inline": True},
                        {"name": "Asking" if project["accepts_money"] else "Wants", "inline": True,
                         "value": f"₹{project['funding_needed']:,}" if project["accepts_money"] else wants}],
             "footer": f"by @{ctx.username} · open the post to sponsor"}
    if project["accepts_money"]:
        embed["progress"] = {"value": raised, "goal": project["funding_needed"],
                             "label": f"₹{raised:,} sponsored of ₹{project['funding_needed']:,}"}
    say(ctx, embed=embed)


def cmd_funding(ctx):
    from servers.routes import goal_progress
    db = ctx.db
    goal = goal_progress(db, ctx.server)
    rows = db.execute("""SELECT p.id, p.title, u.username, COALESCE(SUM(i.amount), 0) AS raised
                         FROM projects p JOIN users u ON u.id = p.innovator_id
                         LEFT JOIN interests i ON i.project_id = p.id AND i.status = 'accepted'
                         WHERE p.innovator_id IN (SELECT user_id FROM server_members WHERE server_id = ?)
                         GROUP BY p.id ORDER BY raised DESC LIMIT 5""", (ctx.server["id"],)).fetchall()
    embed = {"title": "💸 Funding in this server", "color": "#0ea5e9",
             "description": "\n".join(f"{i + 1}. {r['title']} · @{r['username']} · ₹{r['raised']:,}" for i, r in enumerate(rows)) or "No projects from members yet."}
    if goal:
        embed["progress"] = {"value": goal["raised"], "goal": goal["goal"], "label": f"{goal['title']}: ₹{goal['raised']:,} of ₹{goal['goal']:,}"}
    say(ctx, embed=embed)


def cmd_status(ctx):
    from servers import projectlink
    embed = projectlink.status_embed(ctx.db, ctx.server)
    if embed is None:
        return whisper(ctx, "This server isn't linked to a project. Open a post and press 'Create project server'.")
    say(ctx, embed=embed)


HANDLERS = {("scout", "status"): cmd_status, ("herald", "rules"): cmd_rules, ("herald", "welcome"): cmd_welcome, ("dicey", "roll"): cmd_roll,
            ("dicey", "coin"): cmd_coin, ("dicey", "8ball"): cmd_8ball, ("dicey", "choose"): cmd_choose,
            ("pollster", "poll"): cmd_poll, ("nudge", "remind"): cmd_remind, ("nudge", "reminders"): cmd_reminders,
            ("sparks", "rank"): cmd_rank, ("sparks", "top"): cmd_top, ("scout", "pitch"): cmd_pitch,
            ("scout", "funding"): cmd_funding}


# ---------- your own companions: call their webhook when someone uses a command ----------

def safe_url(url):
    from urllib.parse import urlparse
    parts = urlparse(url)
    host = (parts.hostname or "")
    return parts.scheme in ("http", "https") and bool(host) and not host.startswith("169.254.") and not host.startswith("fe80")


def send_to_bot(db, bot, server, channel, user_id, username, command, args):
    if not bot["interaction_url"] or not safe_url(bot["interaction_url"]):
        return tell_only(user_id, channel["id"], f"{bot['name']} has no working interaction URL yet.")
    payload = json.dumps({"type": "command", "command": command, "args": args,
                          "user": {"id": user_id, "username": username}, "server_id": server["id"],
                          "channel_id": channel["id"], "bot_id": bot["id"]}).encode()
    signature = hashlib.sha256((bot["token_hash"] or "").encode() + payload).hexdigest()
    url, bot_user, channel_row = bot["interaction_url"], bot["user_id"], dict(channel)
    app = current_app._get_current_object()

    def work():
        try:
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json",
                                                                     "X-Companion-Signature": signature})
            with urllib.request.urlopen(req, timeout=5) as resp:
                answer = json.loads(resp.read(65536) or b"{}")
        except Exception:
            tell_only(user_id, channel_row["id"], f"{bot['name']} didn't answer in time.")
            return
        if isinstance(answer, dict) and (answer.get("content") or answer.get("embed")):
            with app.app_context():
                from db import get_db
                post_message(get_db(), channel_row, bot_user, str(answer.get("content", "")), embed=answer.get("embed"))
    socketio.start_background_task(work)


# ---------- background worker: reminders and event start-times ----------

_worker_started = False


def ensure_worker(app):
    global _worker_started
    if not _worker_started:
        _worker_started = True
        socketio.start_background_task(_loop, app)


def _loop(app):
    while True:
        socketio.sleep(5)
        try:
            with app.app_context():
                from db import get_db
                tick(get_db())
        except Exception:
            app.logger.exception("companion worker")


def tick(db):
    from servers import projectlink
    projectlink.flush_pending(db)      # queued project messages, once a server and its Scout are ready
    now = int(time.time())
    for r in db.execute("SELECT * FROM reminders WHERE due_at <= ?", (now,)).fetchall():
        db.execute("DELETE FROM reminders WHERE id = ?", (r["id"],))
        db.commit()
        bots = bots_of(db, r["server_id"], "nudge")
        channel = db.execute("SELECT * FROM server_channels WHERE id = ?", (r["channel_id"],)).fetchone()
        if bots and channel:
            post_message(db, channel, bots[0]["user_id"], f"⏰ @{live.username(db, r['user_id'])} reminder: {r['text']}")
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    for ev in db.execute("SELECT * FROM server_events WHERE notified = 0 AND starts_at <= ?", (stamp,)).fetchall():
        db.execute("UPDATE server_events SET notified = 1 WHERE id = ?", (ev["id"],))
        db.commit()
        going = [x["user_id"] for x in db.execute("SELECT user_id FROM event_rsvps WHERE event_id = ?", (ev["id"],))]
        live.notify(going or [ev["creator_id"]], "server", f"🎉 {ev['title']} is starting", ev["description"] or "It starts now!",
                    f"/servers/{ev['server_id']}")
        heralds = bots_of(db, ev["server_id"], "herald")
        channel = (db.execute("SELECT * FROM server_channels WHERE id = ?", (ev["channel_id"],)).fetchone() if ev["channel_id"] else None) \
            or first_text_channel(db, ev["server_id"], "announcements")
        if heralds and channel:
            post_message(db, channel, heralds[0]["user_id"], "", embed={"title": f"🎉 {ev['title']} is starting now",
                         "description": ev["description"], "color": "#f59e0b", "footer": f"{len(going)} interested"})
