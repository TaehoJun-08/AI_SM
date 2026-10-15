from flask import Blueprint, redirect, url_for, flash, session, abort, request
from db import get_db
from helpers import login_required
import live

interests_bp = Blueprint("interests", __name__, url_prefix="/interests")


def go_back(fallback):
    return redirect(request.referrer or fallback)


@interests_bp.route("/post/<int:post_id>", methods=["POST"])
@login_required
def offer(post_id):
    """Anyone can offer to sponsor anyone else's post."""
    me = session["user_id"]
    db = get_db()
    post = db.execute("""SELECT p.id, p.innovator_id, u.accepts_offers FROM projects p
                         JOIN users u ON u.id = p.innovator_id WHERE p.id = ?""", (post_id,)).fetchone()
    if post is None:
        abort(404)
    amount = request.form.get("amount", "").strip()
    message = request.form.get("message", "").strip()[:300]
    if post["innovator_id"] == me:
        flash("You can't sponsor your own project.")
    elif not post["accepts_offers"]:
        flash("This creator has paused sponsorship offers for now.")
    elif not valid_amount(amount):
        flash("Enter the amount you'd like to give as a whole number of rupees.")
    else:
        reopened = db.execute(
            """UPDATE interests SET status = 'pending', amount = ?, message = ?
               WHERE sponsor_id = ? AND project_id = ? AND status = 'withdrawn'""",
            (int(amount), message, me, post_id)).rowcount
        result = db.execute(
            """INSERT OR IGNORE INTO interests (sponsor_id, project_id, amount, message)
               VALUES (?, ?, ?, ?)""", (me, post_id, int(amount), message))
        db.commit()
        live.offer_changed(post_id, me, post["innovator_id"])
        if result.rowcount or reopened:
            live.notify([post["innovator_id"]], "activity", "💸 New sponsorship offer",
                        f"@{live.username(db, me)} offered ₹{int(amount):,}", "/activity", key="activity")
        flash("Offer sent! 💌 You'll hear back in your activity." if result.rowcount or reopened
              else "You've already made an offer on this project.")
    return go_back(url_for("feed.post", post_id=post_id))


def valid_amount(text):
    return text.isdecimal() and 0 < int(text) <= 10_000_000_000


@interests_bp.route("/<int:interest_id>/edit", methods=["POST"])
@login_required
def edit(interest_id):
    """The sponsor changes the amount or message of their own offer (pending or accepted)."""
    me = session["user_id"]
    db = get_db()
    offer = db.execute(
        """SELECT i.*, p.title, p.innovator_id FROM interests i JOIN projects p ON p.id = i.project_id
           WHERE i.id = ? AND i.sponsor_id = ? AND i.status IN ('pending', 'accepted')""", (interest_id, me)).fetchone()
    if offer is None:
        abort(404)
    amount = request.form.get("amount", "").strip()
    message = request.form.get("message", "").strip()[:300]
    if not valid_amount(amount):
        flash("Enter the amount as a whole number of rupees.")
        return go_back(url_for("feed.activity"))
    amount = int(amount)
    if amount == offer["amount"] and message == offer["message"]:
        flash("Nothing changed.")
        return go_back(url_for("feed.activity"))
    db.execute("UPDATE interests SET amount = ?, message = ? WHERE id = ?", (amount, message, interest_id))
    if offer["status"] == "accepted" and amount != offer["amount"]:
        # Tell the owner in their chat, so a change in funding is never a surprise.
        a, b = min(me, offer["innovator_id"]), max(me, offer["innovator_id"])
        thread = db.execute("SELECT id FROM dm_threads WHERE user_a = ? AND user_b = ?", (a, b)).fetchone()
        if thread:
            verb = "increased" if amount > (offer["amount"] or 0) else "reduced"
            note = db.execute("INSERT INTO dm_messages (thread_id, sender_id, body) VALUES (?, ?, ?)",
                              (thread["id"], me, f"✏️ I {verb} my sponsorship for “{offer['title']}”: "
                                                 f"₹{offer['amount']:,} → ₹{amount:,}.")).lastrowid
    db.commit()
    if offer["status"] == "accepted" and amount != offer["amount"] and thread:
        live.dm_note(db, thread["id"], note)
    live.offer_changed(offer["project_id"], me, offer["innovator_id"])
    live.notify([offer["innovator_id"]], "activity", "✏️ Offer changed",
                f"@{live.username(db, me)} changed their offer for {offer['title']}", "/activity", key="activity")
    flash("Your offer was updated. ✏️")
    return go_back(url_for("feed.activity"))


@interests_bp.route("/<int:interest_id>/withdraw", methods=["POST"])
@login_required
def withdraw(interest_id):
    """The sponsor takes back an offer. Pending ones simply disappear; an accepted
    sponsorship is kept as 'withdrawn' and the owner is told in the chat."""
    me = session["user_id"]
    db = get_db()
    offer = db.execute(
        """SELECT i.*, p.title, p.innovator_id FROM interests i JOIN projects p ON p.id = i.project_id
           WHERE i.id = ? AND i.sponsor_id = ? AND i.status IN ('pending', 'accepted')""",
        (interest_id, me)).fetchone()
    if offer is None:
        abort(404)
    if offer["status"] == "pending":
        db.execute("DELETE FROM interests WHERE id = ?", (interest_id,))
        flash("Offer withdrawn.")
    else:
        db.execute("UPDATE interests SET status = 'withdrawn' WHERE id = ?", (interest_id,))
        a, b = min(me, offer["innovator_id"]), max(me, offer["innovator_id"])
        thread = db.execute("SELECT id FROM dm_threads WHERE user_a = ? AND user_b = ?", (a, b)).fetchone()
        if thread:
            note = db.execute("INSERT INTO dm_messages (thread_id, sender_id, body) VALUES (?, ?, ?)",
                              (thread["id"], me, f"↩️ I've withdrawn my sponsorship of ₹{offer['amount']:,} "
                                                 f"for “{offer['title']}”. Sorry for the change of plans.")).lastrowid
        flash("Your sponsorship was withdrawn and the owner was told.")
    db.commit()
    if offer["status"] == "accepted" and thread:
        live.dm_note(db, thread["id"], note)
    live.offer_changed(offer["project_id"], me, offer["innovator_id"])
    live.notify([offer["innovator_id"]], "activity", "↩️ Offer withdrawn",
                f"@{live.username(db, me)} withdrew their offer for {offer['title']}", "/activity", key="activity")
    return go_back(url_for("feed.activity"))


@interests_bp.route("/<int:interest_id>/<action>", methods=["POST"])
@login_required
def respond(interest_id, action):
    """The post's owner accepts or declines an offer. Accepting opens a chat."""
    if action not in ("accept", "decline"):
        abort(404)
    me = session["user_id"]
    db = get_db()
    offer = db.execute(
        """SELECT i.*, p.title FROM interests i JOIN projects p ON p.id = i.project_id
           WHERE i.id = ? AND p.innovator_id = ? AND i.status = 'pending'""",
        (interest_id, me),
    ).fetchone()
    if offer is None:
        abort(404)
    if action == "decline":
        db.execute("UPDATE interests SET status = 'declined', responded_at = datetime('now','localtime') WHERE id = ?",
                   (interest_id,))
        db.commit()
        live.offer_changed(offer["project_id"], me, offer["sponsor_id"])
        live.notify([offer["sponsor_id"]], "activity", "Offer declined",
                    f"Your offer for {offer['title']} was declined", "/activity", key="activity")
        flash("Offer declined.")
        return redirect(url_for("feed.activity"))

    db.execute("UPDATE interests SET status = 'accepted', responded_at = datetime('now','localtime') WHERE id = ?",
               (interest_id,))
    a, b = min(me, offer["sponsor_id"]), max(me, offer["sponsor_id"])
    db.execute("INSERT OR IGNORE INTO dm_threads (user_a, user_b, requested_by, status) "
               "VALUES (?, ?, ?, 'accepted')", (a, b, offer["sponsor_id"]))
    db.execute("UPDATE dm_threads SET status = 'accepted' WHERE user_a = ? AND user_b = ?", (a, b))
    thread = db.execute("SELECT id FROM dm_threads WHERE user_a = ? AND user_b = ?", (a, b)).fetchone()
    note = f"💸 Sponsorship accepted for “{offer['title']}”: ₹{offer['amount']:,}."
    if offer["message"]:
        note += f" “{offer['message']}”"
    note_id = db.execute("INSERT INTO dm_messages (thread_id, sender_id, body) VALUES (?, ?, ?)",
                         (thread["id"], offer["sponsor_id"], note[:2000])).lastrowid
    db.commit()
    live.dm_note(db, thread["id"], note_id, notify=False)   # the owner just did this, only the sponsor hears about it
    live.inbox_changed(thread["id"], reload=True)
    live.offer_changed(offer["project_id"], me, offer["sponsor_id"])
    live.notify([offer["sponsor_id"]], "activity", "🎉 Offer accepted",
                f"@{live.username(db, me)} accepted your offer for {offer['title']}", f"/messages/{thread['id']}", key="activity")
    from servers import companions
    companions.on_sponsorship(db, offer["project_id"], offer["sponsor_id"], offer["amount"])
    flash("You're connected! 🎉 Say hello.")
    return redirect(url_for("dms.thread", thread_id=thread["id"]))
