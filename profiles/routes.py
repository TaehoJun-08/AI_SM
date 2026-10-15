from flask import Blueprint, render_template, redirect, url_for, abort
from db import get_db
from helpers import login_required

profiles_bp = Blueprint("profiles", __name__)


@profiles_bp.route("/u/<username>")
@login_required
def public(username):
    db = get_db()
    person = db.execute(
        """SELECT id, full_name, username, bio, avatar, accepts_offers, created_at
           FROM users WHERE username = ? AND is_bot = 0""",
        (username,),
    ).fetchone()
    if person is None:
        abort(404)
    posts = db.execute(
        """SELECT p.id, p.title, p.domain,
             (SELECT path FROM post_media WHERE post_id = p.id ORDER BY position, id LIMIT 1) AS media_path,
             (SELECT type FROM post_media WHERE post_id = p.id ORDER BY position, id LIMIT 1) AS media_type,
             (SELECT COUNT(*) FROM post_media WHERE post_id = p.id) AS media_count,
             (SELECT COUNT(*) FROM likes WHERE post_id = p.id) AS like_count,
             (SELECT COUNT(*) FROM comments WHERE post_id = p.id) AS comment_count
           FROM projects p WHERE p.innovator_id = ? ORDER BY p.id DESC""",
        (person["id"],),
    ).fetchall()
    stats = {
        "posts": len(posts),
        "likes": sum(p["like_count"] for p in posts),
        "sponsors": db.execute(
            """SELECT COUNT(*) FROM interests i JOIN projects p ON p.id = i.project_id
               WHERE p.innovator_id = ? AND i.status = 'accepted'""", (person["id"],)).fetchone()[0],
    }
    return render_template("profiles/public.html", person=person, posts=posts, stats=stats)


@profiles_bp.route("/profile")
@login_required
def edit():
    """Old address of the profile editor: it now lives in Settings."""
    return redirect(url_for("settings.index"))
