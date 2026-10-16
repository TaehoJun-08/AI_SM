from flask import Blueprint, redirect, url_for, flash, session, abort, request
from db import get_db
from helpers import login_required
import deals
import live

interests_bp = Blueprint("interests", __name__, url_prefix="/interests")


def go_back(fallback):
    return redirect(request.referrer or fallback)


def dm_thread_note(db, me, other, body):
    """Say something in the chat between two people (if they have one). Returns (thread id, message id) or None."""
    a, b = min(me, other), max(me, other)
    thread = db.execute("SELECT id FROM dm_threads WHERE user_a = ? AND user_b = ?", (a, b)).fetchone()
    if thread is None:
        return None
    note = db.execute("INSERT INTO dm_messages (thread_id, sender_id, body) VALUES (?, ?, ?)",
                      (thread["id"], me, body[:2000])).lastrowid
    return thread["id"], note


@interests_bp.route("/post/<int:post_id>", methods=["POST"])
@login_required
def offer(post_id):
    """Anyone can offer to sponsor anyone else's post, with money, goods and/or a free-sponsor pledge."""
    me = session["user_id"]
    db = get_db()
    post = db.execute("""SELECT p.*, u.accepts_offers FROM projects p
                         JOIN users u ON u.id = p.innovator_id WHERE p.id = ?""", (post_id,)).fetchone()
    if post is None:
        abort(404)
    message = request.form.get("message", "").strip()[:300]
    terms, error = deals.parse_offer(request.form, post)
    if post["innovator_id"] == me:
        flash("You can't sponsor your own project.")
    elif not post["accepts_offers"]:
        flash("This creator has paused sponsorship offers for now.")
    elif error:
        flash(error)
    else:
        reopened = db.execute(
            """UPDATE interests SET status = 'pending', amount = ?, goods = ?, pilot = ?, message = ?, turn = 'creator',
                      last_actor = ?, responded_at = datetime('now','localtime')
               WHERE sponsor_id = ? AND project_id = ? AND status = 'withdrawn'""",
            (terms["money"] or None, terms["goods"], terms["pilot"], message, me, me, post_id)).rowcount
        result = db.execute(
            """INSERT OR IGNORE INTO interests (sponsor_id, project_id, amount, goods, pilot, message, last_actor)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (me, post_id, terms["money"] or None, terms["goods"], terms["pilot"], message, me))
        if result.rowcount or reopened:
            interest_id = db.execute("SELECT id FROM interests WHERE sponsor_id = ? AND project_id = ?", (me, post_id)).fetchone()["id"]
            deals.log_round(db, interest_id, me, "offer", terms, note=message)
        db.commit()
        live.offer_changed(post_id, me, post["innovator_id"])
        if result.rowcount or reopened:
            live.notify([post["innovator_id"]], "activity", "💸 New sponsorship offer",
                        f"@{live.username(db, me)} offered {deals.describe(terms)}", f"/deals/{interest_id}", key="activity")
        flash("Offer sent! 💌 You'll hear back in your activity." if result.rowcount or reopened
              else "You've already made an offer on this project.")
    return go_back(url_for("feed.post", post_id=post_id))


@interests_bp.route("/<int:interest_id>/edit", methods=["POST"])
@login_required
def edit(interest_id):
    """The sponsor changes the terms or message of their own offer (while the creator hasn't answered, or once agreed)."""
    me = session["user_id"]
    db = get_db()
    offer = db.execute(
        """SELECT i.*, p.title, p.innovator_id, p.accepts_money, p.accepts_goods, p.accepts_pilot
           FROM interests i JOIN projects p ON p.id = i.project_id
           WHERE i.id = ? AND i.sponsor_id = ? AND i.status IN ('pending', 'accepted')""", (interest_id, me)).fetchone()
    if offer is None:
        abort(404)
    if offer["status"] == "pending" and offer["turn"] == "sponsor":
        flash("The creator sent a counter-offer. Answer it in the deal room.")
        return redirect(url_for("deals.room", interest_id=interest_id))
    message = request.form.get("message", "").strip()[:300]
    terms, error = deals.parse_offer(request.form, offer)
    if error:
        flash(error)
        return go_back(url_for("feed.activity"))
    before = deals.terms_of(offer)
    if terms == before and message == offer["message"]:
        flash("Nothing changed.")
        return go_back(url_for("feed.activity"))
    db.execute("UPDATE interests SET amount = ?, goods = ?, pilot = ?, message = ?, last_actor = ?, "
               "responded_at = datetime('now','localtime') WHERE id = ?",
               (terms["money"] or None, terms["goods"], terms["pilot"], message, me, interest_id))
    deals.log_round(db, interest_id, me, "edit", terms, f"{deals.describe(before)} → {deals.describe(terms)}", message)
    chat = None
    if offer["status"] == "accepted" and terms != before:
        # Tell the owner in their chat, so a change is never a surprise.
        chat = dm_thread_note(db, me, offer["innovator_id"],
                              f"✏️ I changed my sponsorship for “{offer['title']}”: {deals.describe(before)} → {deals.describe(terms)}.")
    db.commit()
    if chat:
        live.dm_note(db, *chat)
    live.offer_changed(offer["project_id"], me, offer["innovator_id"])
    live.notify([offer["innovator_id"]], "activity", "✏️ Offer changed",
                f"@{live.username(db, me)} changed their offer for {offer['title']}", f"/deals/{interest_id}", key="activity")
    flash("Your offer was updated. ✏️")
    return go_back(url_for("feed.activity"))


@interests_bp.route("/<int:interest_id>/withdraw", methods=["POST"])
@login_required
def withdraw(interest_id):
    """The sponsor takes back an offer. Offers nobody has answered simply disappear; anything already
    discussed or agreed is kept as 'withdrawn' and the owner is told in the chat."""
    me = session["user_id"]
    db = get_db()
    offer = db.execute(
        """SELECT i.*, p.title, p.innovator_id FROM interests i JOIN projects p ON p.id = i.project_id
           WHERE i.id = ? AND i.sponsor_id = ? AND i.status IN ('pending', 'accepted')""",
        (interest_id, me)).fetchone()
    if offer is None:
        abort(404)
    talked = db.execute("SELECT COUNT(*) FROM deal_rounds WHERE interest_id = ?", (interest_id,)).fetchone()[0] > 1
    chat = None
    if offer["status"] == "pending" and not talked:
        db.execute("DELETE FROM interests WHERE id = ?", (interest_id,))
        flash("Offer withdrawn.")
    else:
        db.execute("UPDATE interests SET status = 'withdrawn', last_actor = ? WHERE id = ?", (me, interest_id))
        deals.log_round(db, interest_id, me, "withdraw", deals.terms_of(offer))
        if offer["status"] == "accepted":
            chat = dm_thread_note(db, me, offer["innovator_id"],
                                  f"↩️ I've withdrawn my sponsorship ({deals.describe(offer)}) for “{offer['title']}”. "
                                  "Sorry for the change of plans.")
        flash("Your sponsorship was withdrawn and the owner was told." if offer["status"] == "accepted" else "Offer withdrawn.")
    db.commit()
    if chat:
        live.dm_note(db, *chat)
    live.offer_changed(offer["project_id"], me, offer["innovator_id"])
    live.notify([offer["innovator_id"]], "activity", "↩️ Offer withdrawn",
                f"@{live.username(db, me)} withdrew their offer for {offer['title']}", "/activity", key="activity")
    return go_back(url_for("feed.activity"))


@interests_bp.route("/<int:interest_id>/<action>", methods=["POST"])
@login_required
def respond(interest_id, action):
    """Quick answer for the post's owner: accept everything or decline everything. (Part by part,
    counters and notes happen in the deal room.)"""
    if action not in ("accept", "decline"):
        abort(404)
    me = session["user_id"]
    db = get_db()
    offer = deals.load(db, interest_id)
    if offer is None or offer["innovator_id"] != me:
        abort(404)
    parts = {p: action for p in deals.PARTS if deals.terms_of(offer)[p]}
    ok, message, thread = deals.respond(db, offer, me, parts)
    flash(message)
    if ok and thread:
        return redirect(url_for("dms.thread", thread_id=thread))
    return redirect(url_for("feed.activity"))
