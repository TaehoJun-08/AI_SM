"""Milestone escrow (simulated: no real money moves). Money a sponsor agrees to give isn't handed over in one go.
It's locked, and each milestone's share is released only after the creator uploads proof that the work is done
and sponsors holding more than half of the money approve it. If half or more turn it down, the proof goes back
to the creator with their notes. A payment gateway would sit behind exactly this bookkeeping."""
from flask import Blueprint, request, redirect, url_for, flash, session, abort
from db import get_db
from helpers import login_required
from media import save_media, delete_media
import live
from servers import projectlink

escrow_bp = Blueprint("escrow", __name__, url_prefix="/escrow")

STATE_TEXT = {"open": "not funded yet", "funded": "🔒 locked, waiting for proof", "review": "🔍 sponsors are reviewing the proof",
              "released": "💸 released"}


def backers(db, project_id):
    """{sponsor id: rupees} for every agreed deal with money in it."""
    return {r["sponsor_id"]: r["amount"] for r in db.execute(
        "SELECT sponsor_id, amount FROM interests WHERE project_id = ? AND status = 'accepted' AND amount > 0",
        (project_id,))}


def percent(part, whole):
    return round(part * 100 / whole) if whole else 0


def ledger(db, project_id, me):
    """Everything the post page shows about the escrow: the totals, and each milestone with its vote so far."""
    money = backers(db, project_id)
    total = sum(money.values())
    items = projectlink.milestones(db, project_id, total)
    for m in items:
        votes = db.execute("""SELECT v.*, u.username FROM milestone_votes v JOIN users u ON u.id = v.sponsor_id
                              WHERE v.milestone_id = ? ORDER BY v.created_at""", (m["id"],)).fetchall()
        m["yes"] = percent(sum(money.get(v["sponsor_id"], 0) for v in votes if v["vote"] == "approve"), total)
        m["no"] = percent(sum(money.get(v["sponsor_id"], 0) for v in votes if v["vote"] == "reject"), total)
        m["my_vote"] = next((v["vote"] for v in votes if v["sponsor_id"] == me), None)
        m["notes"] = [v for v in votes if v["note"]]
        m["text"] = STATE_TEXT[m["state"]]
    released = min(sum(m["share"] for m in items if m["state"] == "released"), total)
    top = max((m["amount"] for m in items), default=0)
    return {"milestones": items, "raised": total, "released": released, "locked": total - released,
            "uncovered": max(total - top, 0), "backer": me in money, "my_money": money.get(me, 0)}


def load(db, milestone_id):
    row = db.execute("""SELECT m.*, p.title AS project_title, p.innovator_id FROM project_milestones m
                        JOIN projects p ON p.id = m.project_id WHERE m.id = ?""", (milestone_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def state_of(db, milestone):
    for m in projectlink.milestones(db, milestone["project_id"]):
        if m["id"] == milestone["id"]:
            return m
    abort(404)


@escrow_bp.route("/milestone/<int:milestone_id>/proof", methods=["POST"])
@login_required
def submit_proof(milestone_id):
    """The creator shows the milestone's work is done, so its sponsors can release the money."""
    db = get_db()
    milestone = load(db, milestone_id)
    if milestone["innovator_id"] != session["user_id"]:
        abort(404)
    m = state_of(db, milestone)
    proof = request.form.get("proof", "").strip()[:800]
    if m["state"] not in ("funded", "review"):
        flash("Proof can only be sent once a milestone is funded and before its money is released.")
        return projectlink.back_to_post(milestone["project_id"])
    if not proof:
        flash("Describe what was done, so sponsors know what they're approving.")
        return projectlink.back_to_post(milestone["project_id"])
    image = milestone["proof_image"]
    try:
        path, kind = save_media(request.files.get("image"))
        if kind == "video":
            delete_media(path)
            flash("Proof takes a photo, not a video. The text was still sent.")
        elif path:
            delete_media(image)
            image = path
    except ValueError as e:
        flash(str(e))
    db.execute("""UPDATE project_milestones SET proof = ?, proof_image = ?, proof_at = datetime('now','localtime'),
                  sent_back = NULL WHERE id = ?""", (proof, image, milestone_id))
    db.execute("DELETE FROM milestone_votes WHERE milestone_id = ?", (milestone_id,))     # a new proof is a new vote
    db.commit()
    who = list(backers(db, milestone["project_id"]))
    live.notify(who, "activity", "🔍 Proof to review",
                f"{milestone['project_title']}: “{milestone['title']}” is done. Review it to release ₹{m['share']:,}.",
                f"/p/{milestone['project_id']}")
    projectlink.project_post(db, milestone["project_id"], embed={
        "title": f"🔍 Proof submitted: {milestone['title']}", "description": proof, "color": "#0ea5e9",
        **({"image": "/static/" + image} if image else {}),
        "footer": f"Sponsors vote to release ₹{m['share']:,} from escrow"})
    live.touch(["posts"])
    flash("Proof sent. Your sponsors were asked to review it. 🔍")
    return projectlink.back_to_post(milestone["project_id"])


@escrow_bp.route("/milestone/<int:milestone_id>/vote", methods=["POST"])
@login_required
def vote(milestone_id):
    """A sponsor with money in the project approves the proof or sends it back. Votes count by money given."""
    db = get_db()
    me = session["user_id"]
    milestone = load(db, milestone_id)
    money = backers(db, milestone["project_id"])
    if me not in money:
        abort(404)
    m = state_of(db, milestone)
    choice = request.form.get("vote")
    note = " ".join(request.form.get("note", "").split())[:300]
    if m["state"] != "review" or choice not in ("approve", "reject"):
        flash("This milestone isn't waiting for a review right now.")
        return projectlink.back_to_post(milestone["project_id"])
    if choice == "reject" and not note:
        flash("Say what's missing, so the creator knows what to fix.")
        return projectlink.back_to_post(milestone["project_id"])
    db.execute("""INSERT INTO milestone_votes (milestone_id, sponsor_id, vote, note) VALUES (?, ?, ?, ?)
                  ON CONFLICT (milestone_id, sponsor_id) DO UPDATE SET vote = excluded.vote, note = excluded.note,
                  created_at = datetime('now','localtime')""", (milestone_id, me, choice, note))
    db.commit()
    total = sum(money.values())
    votes = db.execute("SELECT sponsor_id, vote, note FROM milestone_votes WHERE milestone_id = ?", (milestone_id,)).fetchall()
    yes = sum(money.get(v["sponsor_id"], 0) for v in votes if v["vote"] == "approve")
    no = sum(money.get(v["sponsor_id"], 0) for v in votes if v["vote"] == "reject")
    if yes * 2 > total:
        release(db, milestone, m["share"])
        flash(f"Approved. ₹{m['share']:,} was released to the creator. 💸")
    elif no * 2 >= total:
        send_back(db, milestone, [v["note"] for v in votes if v["vote"] == "reject" and v["note"]])
        flash("The proof went back to the creator with your notes.")
    else:
        flash("Your vote is in. The money is released once sponsors holding more than half of it approve.")
    live.touch(["posts"])
    return projectlink.back_to_post(milestone["project_id"])


def release(db, milestone, share):
    db.execute("UPDATE project_milestones SET released_at = datetime('now','localtime') WHERE id = ?", (milestone["id"],))
    db.commit()
    live.notify([milestone["innovator_id"]], "activity", "💸 Money released",
                f"Sponsors approved “{milestone['title']}”: ₹{share:,} is yours.", f"/p/{milestone['project_id']}")
    projectlink.project_post(db, milestone["project_id"], embed={
        "title": f"💸 ₹{share:,} released: {milestone['title']}", "color": "#10b981",
        "description": "Sponsors approved the proof, so this milestone's money left escrow."})
    projectlink.notify_followers(db, milestone["project_id"], f"💸 {milestone['project_title']}",
                                 f"Milestone done and paid: {milestone['title']}", skip=(milestone["innovator_id"],))


def send_back(db, milestone, notes):
    db.execute("UPDATE project_milestones SET proof_at = NULL, sent_back = ? WHERE id = ?",
               (" · ".join(notes)[:600] or "Sponsors asked for more proof.", milestone["id"]))
    db.execute("DELETE FROM milestone_votes WHERE milestone_id = ?", (milestone["id"],))
    db.commit()
    live.notify([milestone["innovator_id"]], "activity", "↩️ Proof sent back",
                f"Sponsors want more on “{milestone['title']}”. Their notes are on the post.", f"/p/{milestone['project_id']}")
