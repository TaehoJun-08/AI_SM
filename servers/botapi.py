"""The HTTP API for your own companions and webhooks.

  Authorization: Bot <token>          (custom companions)
  GET  /api/bot/me                    who am I, which channels can I post in
  POST /api/bot/messages              {"channel_id": 3, "content": "hi", "embed": {...}}
  PUT  /api/bot/commands              {"commands": [{"name": "ping", "description": "Say pong"}]}
  POST /api/hooks/<bot id>/<token>    {"content": "hi", "embed": {...}}   (webhooks: one channel, no header)
"""
import time
from flask import Blueprint, jsonify, request
from db import get_db
from servers.messages import post_message
from servers.companions import token_hash

api_bp = Blueprint("botapi", __name__, url_prefix="/api")
_recent = {}          # bot id -> send times (a small rate limit: 20 messages per 10 seconds)
POSTABLE = ("text", "announcement", "voice", "thread")


def rate_ok(bot_id):
    now = time.time()
    times = [t for t in _recent.get(bot_id, []) if now - t < 10]
    if len(times) >= 20:
        _recent[bot_id] = times
        return False
    _recent[bot_id] = times + [now]
    return True


def bot_from_header():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bot "):
        return None
    return get_db().execute("SELECT * FROM bots WHERE token_hash = ? AND kind = 'custom'",
                            (token_hash(header[4:].strip()),)).fetchone()


def post_as(bot, channel_id, data):
    db = get_db()
    channel = db.execute("SELECT * FROM server_channels WHERE id = ? AND server_id = ?", (channel_id, bot["server_id"])).fetchone()
    if channel is None or channel["kind"] not in POSTABLE:
        return jsonify(error="Unknown channel"), 404
    from servers.routes import membership, can_read
    m = membership(bot["server_id"], bot["user_id"])
    if m is None or not can_read(m, channel):
        return jsonify(error="This companion can't see that channel (give it a role)"), 403
    if not rate_ok(bot["id"]):
        return jsonify(error="Slow down"), 429
    content = data.get("content") if isinstance(data.get("content"), str) else ""
    message = post_message(db, channel, bot["user_id"], content, embed=data.get("embed"))
    if message is None:
        return jsonify(error="Send content or an embed"), 400
    return jsonify(id=message["id"]), 201


@api_bp.route("/bot/me")
def me():
    bot = bot_from_header()
    if bot is None:
        return jsonify(error="Bad token"), 401
    channels = get_db().execute("SELECT id, name, kind FROM server_channels WHERE server_id = ? AND kind != 'thread'",
                                (bot["server_id"],)).fetchall()
    return jsonify(id=bot["id"], name=bot["name"], server_id=bot["server_id"], channels=[dict(c) for c in channels])


@api_bp.route("/bot/messages", methods=["POST"])
def send():
    bot = bot_from_header()
    if bot is None:
        return jsonify(error="Bad token"), 401
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get("channel_id"), int):
        return jsonify(error="channel_id is required"), 400
    return post_as(bot, data["channel_id"], data)


@api_bp.route("/bot/commands", methods=["PUT", "POST"])
def commands():
    bot = bot_from_header()
    if bot is None:
        return jsonify(error="Bad token"), 401
    items = (request.get_json(silent=True) or {}).get("commands")
    if not isinstance(items, list):
        return jsonify(error="send {\"commands\": [...]}"), 400
    db = get_db()
    db.execute("DELETE FROM bot_commands WHERE bot_id = ?", (bot["id"],))
    saved = 0
    for c in items[:25]:
        name = str(c.get("name", "")).lower().strip().lstrip("/")[:20] if isinstance(c, dict) else ""
        if name and name.replace("_", "").replace("-", "").isalnum():
            db.execute("INSERT OR REPLACE INTO bot_commands (bot_id, name, description) VALUES (?, ?, ?)",
                       (bot["id"], name, str(c.get("description", ""))[:100]))
            saved += 1
    db.commit()
    return jsonify(saved=saved)


@api_bp.route("/hooks/<int:bot_id>/<token>", methods=["POST"])
def webhook(bot_id, token):
    bot = get_db().execute("SELECT * FROM bots WHERE id = ? AND kind = 'webhook' AND token_hash = ?",
                           (bot_id, token_hash(token))).fetchone()
    if bot is None or bot["channel_id"] is None:
        return jsonify(error="Unknown webhook"), 404
    return post_as(bot, bot["channel_id"], request.get_json(silent=True) or request.form.to_dict())
