"""Sponsorship deals. An offer is a bundle of up to three parts: money, goods or services, and a free-sponsor
pledge ("I'll use the product and share feedback"). Both people can answer part by part: accept it, decline it
or counter it. A counter hands the turn to the other person, and this goes on as long as they like. When
nothing is left to counter, the deal is agreed with whatever parts are left."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort
from db import get_db
from helpers import login_required
import live

deals_bp = Blueprint("deals", __name__, url_prefix="/deals")

PARTS = ("money", "goods", "pilot")
MAX_MONEY = 10_000_000_000
LABEL = {"money": "💰 Money", "goods": "🎁 Goods or services", "pilot": "🌱 Free sponsor"}


# ---------- what an offer says ----------

def terms_of(row):
    """{'money': 5000, 'goods': 'hosting', 'pilot': 1} from an interests row or a terms dict."""
    keys = row.keys()
    money = row["money"] if "money" in keys else row["amount"]
    return {"money": int(money or 0), "goods": (row["goods"] or "").strip(), "pilot": int(row["pilot"] or 0)}


def describe(row):
    """'₹5,000 + free hosting + 🌱 free-sponsor pledge' (also used in templates as terms_text)."""
    t = terms_of(row)
    parts = []
    if t["money"]:
        parts.append(f"₹{t['money']:,}")
    if t["goods"]:
        parts.append(t["goods"])
    if t["pilot"]:
        parts.append("🌱 free-sponsor pledge")
    return " + ".join(parts) or "nothing yet"


def kinds_of(project):
    return [k for k in PARTS if project["accepts_" + k]]


def parse_offer(form, project):
    """The parts a sponsor ticked in the dialog -> (terms, error). Only kinds the project accepts count."""
    allowed = kinds_of(project)
    only = len(allowed) == 1                       # a single accepted kind needs no tick box
    terms = {"money": 0, "goods": "", "pilot": 0}
    if "money" in allowed and (only or form.get("use_money")):
        amount = form.get("amount", "").strip()
        if not amount.isdecimal() or not 0 < int(amount) <= MAX_MONEY:
            return None, "Enter the amount as a whole number of rupees."
        terms["money"] = int(amount)
    if "goods" in allowed and (only or form.get("use_goods")):
        goods = " ".join(form.get("goods", "").split())[:200]
        if not goods:
            return None, "Say what goods or services you can give."
        terms["goods"] = goods
    if "pilot" in allowed and (only or form.get("use_pilot")):
        terms["pilot"] = 1
    if not any(terms.values()):
        return None, "Pick at least one way to sponsor."
    return terms, None


# ---------- storing the history ----------

def load(db, interest_id):
    return db.execute(
        """SELECT i.*, p.title, p.innovator_id FROM interests i JOIN projects p ON p.id = i.project_id
           WHERE i.id = ?""", (interest_id,)).fetchone()


def role_of(offer, user_id):
    if user_id == offer["innovator_id"]:
        return "creator"
    return "sponsor" if user_id == offer["sponsor_id"] else None


def log_round(db, interest_id, actor, kind, terms, summary="", note=""):
    db.execute(
        """INSERT INTO deal_rounds (interest_id, actor_id, kind, amount, goods, pilot, summary, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (interest_id, actor, kind, terms["money"] or None, terms["goods"], terms["pilot"], summary, (note or "")[:300]))


def read_decisions(form, offer):
    """The room form -> {part: 'accept' | 'decline' | ('counter', value)} for each part that is on the table."""
    t = terms_of(offer)
    out = {}
    for part in PARTS:
        if not t[part]:
            continue
        choice = form.get("d_" + part, "decline")
        if choice == "counter" and part != "pilot":
            out[part] = ("counter", form.get("v_" + part, "").strip())
        else:
            out[part] = "accept" if choice == "accept" else "decline"
    return out


# ---------- answering an offer ----------

def respond(db, offer, actor, decisions, note=""):
    """Apply one person's answer. Returns (ok, flash message, thread id or None)."""
    role = role_of(offer, actor)
    if role is None or offer["status"] != "pending" or offer["turn"] != role:
        return False, "It isn't your turn on this offer.", None
    current = terms_of(offer)
    kept = {"money": 0, "goods": "", "pilot": 0}
    summary, countered = [], False
    for part in PARTS:
        if not current[part]:
            continue
        what = describe({"money": current["money"] if part == "money" else 0,
                         "goods": current["goods"] if part == "goods" else "", "pilot": current["pilot"] if part == "pilot" else 0})
        choice = decisions.get(part, "decline")
        if isinstance(choice, tuple):
            value = choice[1]
            if part == "money":
                if not value.isdecimal() or not 0 < int(value) <= MAX_MONEY:
                    return False, "Enter the counter amount as a whole number of rupees.", None
                if int(value) != current["money"]:
                    countered = True
                    kept["money"] = int(value)
                    summary.append(f"↔ {what} → ₹{int(value):,}")
                    continue
                choice = "accept"
            else:
                value = " ".join(value.split())[:200]
                if not value:
                    return False, "Say what you'd like to counter with.", None
                if value != current["goods"]:
                    countered = True
                    kept["goods"] = value
                    summary.append(f"↔ {what} → {value}")
                    continue
                choice = "accept"
        if choice == "accept":
            kept[part] = current[part]
            summary.append(f"✅ {what}")
        else:
            summary.append(f"✖ {what}")
    other = "sponsor" if role == "creator" else "creator"
    other_id = offer["sponsor_id"] if role == "creator" else offer["innovator_id"]
    text = " · ".join(summary)
    me_name = live.username(db, actor)

    if not any(kept.values()):                       # nothing left: the deal is over
        status = "declined" if role == "creator" else "withdrawn"
        db.execute("""UPDATE interests SET status = ?, last_actor = ?, responded_at = datetime('now','localtime')
                      WHERE id = ?""", (status, actor, offer["id"]))
        log_round(db, offer["id"], actor, "decline", current, text, note)
        db.commit()
        live.offer_changed(offer["project_id"], actor, other_id)
        live.notify([other_id], "activity", "Offer declined" if role == "creator" else "Offer withdrawn",
                    f"@{me_name} ended the deal for {offer['title']}", f"/deals/{offer['id']}", key="activity")
        return True, "You ended this deal.", None

    if countered:                                    # hand the turn to the other person
        db.execute("""UPDATE interests SET amount = ?, goods = ?, pilot = ?, turn = ?, last_actor = ?,
                      responded_at = datetime('now','localtime') WHERE id = ?""",
                   (kept["money"] or None, kept["goods"], kept["pilot"], other, actor, offer["id"]))
        log_round(db, offer["id"], actor, "counter", kept, text, note)
        db.commit()
        live.offer_changed(offer["project_id"], actor, other_id)
        live.notify([other_id], "activity", "↔ Counter-offer", f"@{me_name} countered on {offer['title']}: {describe(kept)}",
                    f"/deals/{offer['id']}", key="activity")
        return True, "Counter-offer sent. Now it's their turn. ↔", None

    db.execute("""UPDATE interests SET status = 'accepted', amount = ?, goods = ?, pilot = ?, last_actor = ?,
                  responded_at = datetime('now','localtime') WHERE id = ?""",
               (kept["money"] or None, kept["goods"], kept["pilot"], actor, offer["id"]))
    log_round(db, offer["id"], actor, "accept", kept, text, note)
    db.commit()
    thread = _connect(db, offer, actor, other_id, kept, note)
    return True, "Deal agreed! 🎉 You're connected, say hello.", thread


def _connect(db, offer, actor, other_id, terms, note=""):
    """The deal is agreed: open a chat, say so in it, tell the other person and let the project server cheer."""
    sponsor, creator = offer["sponsor_id"], offer["innovator_id"]
    a, b = min(sponsor, creator), max(sponsor, creator)
    db.execute("INSERT OR IGNORE INTO dm_threads (user_a, user_b, requested_by, status) VALUES (?, ?, ?, 'accepted')",
               (a, b, sponsor))
    db.execute("UPDATE dm_threads SET status = 'accepted' WHERE user_a = ? AND user_b = ?", (a, b))
    thread = db.execute("SELECT id FROM dm_threads WHERE user_a = ? AND user_b = ?", (a, b)).fetchone()["id"]
    body = f"🤝 Sponsorship agreed for “{offer['title']}”: {describe(terms)}."
    if note or offer["message"]:
        body += f" “{note or offer['message']}”"
    note_id = db.execute("INSERT INTO dm_messages (thread_id, sender_id, body) VALUES (?, ?, ?)",
                         (thread, sponsor, body[:2000])).lastrowid
    db.commit()
    live.dm_note(db, thread, note_id, notify=False)
    live.inbox_changed(thread, reload=True)
    live.offer_changed(offer["project_id"], actor, other_id)
    live.notify([other_id], "activity", "🎉 Deal agreed", f"@{live.username(db, actor)} agreed on {offer['title']}: {describe(terms)}",
                f"/deals/{offer['id']}", key="activity")
    from servers import companions
    companions.on_sponsorship(db, offer["project_id"], sponsor, terms)
    return thread


# ---------- the deal room page ----------

def room_context(db, offer, me):
    role = role_of(offer, me)
    rounds = [dict(r) for r in db.execute(
        """SELECT r.*, u.username, u.full_name FROM deal_rounds r JOIN users u ON u.id = r.actor_id
           WHERE r.interest_id = ? ORDER BY r.id""", (offer["id"],))]
    if not rounds:                                    # offers made before deals existed
        who = db.execute("SELECT username, full_name FROM users WHERE id = ?", (offer["sponsor_id"],)).fetchone()
        rounds = [{"kind": "offer", "actor_id": offer["sponsor_id"], "username": who["username"], "full_name": who["full_name"],
                   "amount": offer["amount"], "goods": offer["goods"], "pilot": offer["pilot"], "summary": "",
                   "note": offer["message"], "created_at": offer["created_at"]}]
    other_id = offer["sponsor_id"] if role == "creator" else offer["innovator_id"]
    other = db.execute("SELECT id, username, full_name, avatar FROM users WHERE id = ?", (other_id,)).fetchone()
    your_turn = offer["status"] == "pending" and offer["turn"] == role
    return {"offer": offer, "rounds": rounds, "role": role, "other": other, "your_turn": your_turn,
            "terms": terms_of(offer), "labels": LABEL}


@deals_bp.route("/<int:interest_id>")
@login_required
def room(interest_id):
    db = get_db()
    offer = load(db, interest_id)
    if offer is None or role_of(offer, session["user_id"]) is None:
        abort(404)
    return render_template("deals/room.html", **room_context(db, offer, session["user_id"]))


@deals_bp.route("/<int:interest_id>/respond", methods=["POST"])
@login_required
def answer(interest_id):
    db = get_db()
    offer = load(db, interest_id)
    me = session["user_id"]
    if offer is None or role_of(offer, me) is None:
        abort(404)
    ok, message, _ = respond(db, offer, me, read_decisions(request.form, offer), request.form.get("note", "").strip())
    flash(message)
    return redirect(url_for("deals.room", interest_id=interest_id))
