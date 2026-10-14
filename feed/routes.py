from flask import (Blueprint, render_template, request, jsonify,
                   session, abort)
from db import get_db
from helpers import login_required
import live
import unread

feed_bp = Blueprint("feed", __name__)

PAGE_SIZE = 5

POST_SELECT = """
    SELECT p.*, u.username, u.full_name, u.avatar, u.accepts_offers,
      (SELECT COUNT(*) FROM likes WHERE post_id = p.id) AS like_count,
      EXISTS (SELECT 1 FROM likes WHERE post_id = p.id AND user_id = :me) AS liked,
      (SELECT COUNT(*) FROM comments WHERE post_id = p.id) AS comment_count,
      COALESCE((SELECT SUM(amount) FROM interests
                WHERE project_id = p.id AND status = 'accepted'), 0) AS raised,
      (SELECT status FROM interests WHERE project_id = p.id AND sponsor_id = :me) AS my_offer,
      (SELECT id FROM interests WHERE project_id = p.id AND sponsor_id = :me) AS my_offer_id,
      (SELECT amount FROM interests WHERE project_id = p.id AND sponsor_id = :me) AS my_amount,
      (SELECT message FROM interests WHERE project_id = p.id AND sponsor_id = :me) AS my_message
    FROM projects p JOIN users u ON u.id = p.innovator_id
"""


def attach_media(posts):
    """Give each post dict a `media` list (all its photos and videos, in order)."""
    if not posts:
        return
    ids = [p["id"] for p in posts]
    marks = ",".join("?" * len(ids))
    by_post = {i: [] for i in ids}
    for row in get_db().execute(
            f"SELECT post_id, path, type FROM post_media WHERE post_id IN ({marks}) "
            "ORDER BY position, id", ids):
        by_post[row["post_id"]].append(dict(row))
    for p in posts:
        p["media"] = by_post[p["id"]]


def load_posts(where="", params=None, order="p.id DESC", limit=PAGE_SIZE):
    """Posts as dicts, each with its photos and videos attached."""
    params = dict(params or {}, me=session["user_id"])
    rows = get_db().execute(f"{POST_SELECT} {where} ORDER BY {order} LIMIT {int(limit)}", params).fetchall()
    posts = [dict(r) for r in rows]
    attach_media(posts)
    return posts


@feed_bp.route("/")
def index():
    if session.get("user_id") is None:
        return render_template("home.html")
    db = get_db()
    posts = load_posts()
    authors = db.execute(
        """SELECT u.username, u.full_name, u.avatar, MAX(p.id) AS last_post FROM projects p
           JOIN users u ON u.id = p.innovator_id GROUP BY u.id
           ORDER BY last_post DESC LIMIT 12""").fetchall()
    suggested = db.execute(
        """SELECT id, username, full_name, avatar FROM users
           WHERE id != :me
             AND id NOT IN (SELECT CASE WHEN user_a = :me THEN user_b ELSE user_a END FROM dm_threads
                            WHERE :me IN (user_a, user_b) AND status IN ('accepted', 'pending'))
           ORDER BY id DESC LIMIT 5""", {"me": session["user_id"]}).fetchall()
    return render_template("feed/index.html", posts=posts, authors=authors, suggested=suggested)


@feed_bp.route("/feed/more")
@login_required
def more():
    """The next batch of posts, as HTML, for infinite scrolling."""
    before = request.args.get("before", type=int)
    if before is None:
        abort(400)
    posts = load_posts("WHERE p.id < :before", {"before": before})
    return "".join(render_template("feed/_post.html", post=p) for p in posts)


@feed_bp.route("/feed/new")
@login_required
def new_posts():
    """Posts newer than the one at the top of the page, as HTML (pushed by a live event)."""
    after = request.args.get("after", 0, type=int)
    posts = load_posts("WHERE p.id > :after", {"after": after}, limit=20)
    return "".join(render_template("feed/_post.html", post=p) for p in posts)


@feed_bp.route("/p/<int:post_id>/card")
@login_required
def card(post_id):
    """One post card, so a page can refresh its funding bar and buttons live."""
    posts = load_posts("WHERE p.id = :pid", {"pid": post_id})
    if not posts:
        abort(404)
    return render_template("feed/_post.html", post=posts[0])


@feed_bp.route("/p/<int:post_id>")
@login_required
def post(post_id):
    posts = load_posts("WHERE p.id = :pid", {"pid": post_id})
    if not posts:
        abort(404)
    comments = get_db().execute(
        """SELECT c.id, c.user_id, c.body, c.created_at, u.username FROM comments c
           JOIN users u ON u.id = c.user_id WHERE c.post_id = ? ORDER BY c.id""",
        (post_id,)).fetchall()
    return render_template("feed/post.html", post=posts[0], comments=comments)


@feed_bp.route("/p/<int:post_id>/like", methods=["POST"])
@login_required
def like(post_id):
    me = session["user_id"]
    db = get_db()
    if not db.execute("SELECT 1 FROM projects WHERE id = ?", (post_id,)).fetchone():
        abort(404)
    removed = db.execute("DELETE FROM likes WHERE post_id = ? AND user_id = ?", (post_id, me))
    if removed.rowcount == 0:
        db.execute("INSERT INTO likes (post_id, user_id) VALUES (?, ?)", (post_id, me))
    db.commit()
    count = db.execute("SELECT COUNT(*) FROM likes WHERE post_id = ?", (post_id,)).fetchone()[0]
    live.to_feed("post_likes", {"id": post_id, "count": count})
    live.to_users([me], "post_liked", {"id": post_id, "liked": removed.rowcount == 0})
    live.touch(["engagement"])
    post = db.execute("SELECT innovator_id, title FROM projects WHERE id = ?", (post_id,)).fetchone()
    live.to_users([post["innovator_id"]], "activity")
    if removed.rowcount == 0 and post["innovator_id"] != me:
        live.notify([post["innovator_id"]], "activity", "❤️ New like",
                    f"@{live.username(db, me)} liked {post['title']}", f"/p/{post_id}", key="activity")
    return jsonify(liked=removed.rowcount == 0, count=count)


@feed_bp.route("/p/<int:post_id>/comment", methods=["POST"])
@login_required
def comment(post_id):
    body = request.form.get("body", "").strip()
    db = get_db()
    if not db.execute("SELECT 1 FROM projects WHERE id = ?", (post_id,)).fetchone():
        abort(404)
    if not 1 <= len(body) <= 500:
        return jsonify(error="Comments are 1-500 characters."), 400
    cid = db.execute("INSERT INTO comments (post_id, user_id, body) VALUES (?, ?, ?)",
                     (post_id, session["user_id"], body)).lastrowid
    db.commit()
    count = db.execute("SELECT COUNT(*) FROM comments WHERE post_id = ?", (post_id,)).fetchone()[0]
    username = db.execute("SELECT username FROM users WHERE id = ?", (session["user_id"],)).fetchone()[0]
    data = {"id": post_id, "cid": cid, "uid": session["user_id"], "username": username, "body": body, "count": count}
    live.to_feed("post_comment", data)
    live.touch(["engagement"])
    post = db.execute("SELECT innovator_id, title FROM projects WHERE id = ?", (post_id,)).fetchone()
    live.to_users([post["innovator_id"]], "activity")
    if post["innovator_id"] != session["user_id"]:
        live.notify([post["innovator_id"]], "activity", f"💬 New comment on {post['title']}",
                    f"@{username}: {body}", f"/p/{post_id}", key="activity")
    return jsonify(data)


@feed_bp.route("/activity")
@login_required
def activity():
    me = session["user_id"]
    db = get_db()
    unread.mark_activity(me)
    incoming = db.execute(
        """SELECT i.*, p.title, u.username, u.full_name, u.avatar, u.email
           FROM interests i JOIN projects p ON p.id = i.project_id
           JOIN users u ON u.id = i.sponsor_id
           WHERE p.innovator_id = ?
           ORDER BY (i.status = 'pending') DESC, i.id DESC""", (me,)).fetchall()
    sent = db.execute(
        """SELECT i.*, p.title, p.id AS post_id, u.username FROM interests i
           JOIN projects p ON p.id = i.project_id JOIN users u ON u.id = p.innovator_id
           WHERE i.sponsor_id = ? ORDER BY i.id DESC""", (me,)).fetchall()
    events = [dict(r, kind="like") for r in db.execute(
        """SELECT l.created_at, p.id AS post_id, p.title, u.username, '' AS body
           FROM likes l JOIN projects p ON p.id = l.post_id JOIN users u ON u.id = l.user_id
           WHERE p.innovator_id = ? AND l.user_id != ? ORDER BY l.created_at DESC LIMIT 30""",
        (me, me))]
    events += [dict(r, kind="comment") for r in db.execute(
        """SELECT c.created_at, p.id AS post_id, p.title, u.username, c.body
           FROM comments c JOIN projects p ON p.id = c.post_id JOIN users u ON u.id = c.user_id
           WHERE p.innovator_id = ? AND c.user_id != ? ORDER BY c.created_at DESC LIMIT 30""",
        (me, me))]
    events.sort(key=lambda e: e["created_at"], reverse=True)
    return render_template("feed/activity.html", incoming=incoming, sent=sent, events=events[:40])


@feed_bp.route("/search")
@login_required
def search():
    from projects.routes import DOMAINS
    q = request.args.get("q", "").strip()
    domain = request.args.get("domain", "").strip()
    like = "%" + q.lstrip("@") + "%"
    people = []
    where, params = [], {}
    if q:
        people = get_db().execute(
            """SELECT id, username, full_name, bio, avatar FROM users
               WHERE username LIKE :like OR full_name LIKE :like
               ORDER BY username LIMIT 12""", {"like": like}).fetchall()
        where.append("(p.title LIKE :like OR p.summary LIKE :like OR p.description LIKE :like "
                     "OR p.domain LIKE :like OR u.username LIKE :like)")
        params["like"] = like
    if domain:
        where.append("p.domain = :domain")
        params["domain"] = domain
    clause = "WHERE " + " AND ".join(where) if where else ""
    posts = load_posts(clause, params, limit=60)
    for p in posts:
        first = p["media"][0] if p["media"] else None
        p["media_path"], p["media_type"] = (first["path"], first["type"]) if first else (None, None)
        p["media_count"] = len(p["media"])
    return render_template("feed/search.html", q=q, domain=domain, people=people,
                           posts=posts, domains=DOMAINS)
