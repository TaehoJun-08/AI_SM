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

STATE_TEXT = {"open": "not pledged yet", "funded": "🔒 committed, waiting for proof", "review": "🔍 sponsors are reviewing the proof",
              "released": "💸 released"}


def backers(db, project_id):
    """{sponsor id: rupees} for every agreed deal on this project. Goods/pilot-only sponsors are
    included too (amount 0) -- they still get a vote on proof, just no money weight behind it."""
    return {r["sponsor_id"]: (r["amount"] or 0) for r in db.execute(
        "SELECT sponsor_id, amount FROM interests WHERE project_id = ? AND status = 'accepted'",
        (project_id,))}


def percent(part, whole):
    return round(part * 100 / whole) if whole else 0


def ledger(db, project_id, me):
    """Everything the post page shows about the escrow: the totals, and each milestone with its vote so far.
    Release needs a majority of sponsors AND a majority of the money (see vote()) -- both percentages are
    shown so nobody's surprised by which one is still missing."""
    money = backers(db, project_id)
    total = sum(money.values())
    n_backers = len(money)
    items = projectlink.milestones(db, project_id, total)
    for m in items:
        votes = db.execute("""SELECT v.*, u.username FROM milestone_votes v JOIN users u ON u.id = v.sponsor_id
                              WHERE v.milestone_id = ? ORDER BY v.created_at""", (m["id"],)).fetchall()
        approve, reject = [v for v in votes if v["vote"] == "approve"], [v for v in votes if v["vote"] == "reject"]
        m["yes"] = percent(sum(money.get(v["sponsor_id"], 0) for v in approve), total)
        m["no"] = percent(sum(money.get(v["sponsor_id"], 0) for v in reject), total)
        m["yes_people"] = percent(len(approve), n_backers)
        m["no_people"] = percent(len(reject), n_backers)
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
    db.execute("""UPDATE project_milestones SET proof = ?, proof_image = ?, proof_at = to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'),
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
    """Every accepted sponsor on the project (money, goods or pilot) gets one vote on the proof. Releasing
    needs BOTH a majority of sponsors AND a majority of the money -- so neither a single big backer nor a
    crowd of small ones can decide alone."""
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
                  created_at = to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')""", (milestone_id, me, choice, note))
    db.commit()
    total_money = sum(money.values())
    total_people = len(money)
    votes = db.execute("SELECT sponsor_id, vote, note FROM milestone_votes WHERE milestone_id = ?", (milestone_id,)).fetchall()
    yes_money = sum(money.get(v["sponsor_id"], 0) for v in votes if v["vote"] == "approve")
    no_money = sum(money.get(v["sponsor_id"], 0) for v in votes if v["vote"] == "reject")
    yes_people = sum(1 for v in votes if v["vote"] == "approve")
    no_people = sum(1 for v in votes if v["vote"] == "reject")
    money_approves, people_approve = yes_money * 2 > total_money, yes_people * 2 > total_people
    money_rejects, people_reject = no_money * 2 >= total_money, no_people * 2 >= total_people
    if money_approves and people_approve:
        release(db, milestone, m["share"])
        flash(f"Approved by both sponsors and money. ₹{m['share']:,} was released to the creator. 💸")
    elif money_rejects or people_reject:
        send_back(db, milestone, [v["note"] for v in votes if v["vote"] == "reject" and v["note"]])
        flash("The proof went back to the creator with your notes.")
    else:
        flash("Your vote is in. It releases once more than half of all sponsors AND more than half of the money approve.")
    live.touch(["posts"])
    return projectlink.back_to_post(milestone["project_id"])


@escrow_bp.route("/project/<int:project_id>/withdraw", methods=["POST"])
@login_required
def withdraw(project_id):
    """A sponsor reclaims a stuck pledge: a milestone went overdue with no proof ever submitted, and
    nothing has reached review or been paid out yet, so withdrawing can't undo anyone else's approval."""
    db = get_db()
    me = session["user_id"]
    items = projectlink.milestones(db, project_id)
    overdue = any(m["overdue"] for m in items)
    moving = any(m["state"] in ("review", "released") for m in items)
    if not overdue or moving:
        flash("A pledge can only be withdrawn once a milestone is overdue and nothing has been proven or paid out yet.")
        return projectlink.back_to_post(project_id)
    interest = db.execute("SELECT id FROM interests WHERE sponsor_id = ? AND project_id = ? AND status = 'accepted'",
                          (me, project_id)).fetchone()
    if interest is None:
        abort(404)
    db.execute("UPDATE interests SET status = 'withdrawn' WHERE id = ?", (interest["id"],))
    db.commit()
    project = db.execute("SELECT title, innovator_id FROM projects WHERE id = ?", (project_id,)).fetchone()
    live.notify([project["innovator_id"]], "activity", "↩️ A pledge was withdrawn",
                f"A sponsor pulled their pledge from “{project['title']}” after a milestone went overdue with no proof submitted.",
                f"/p/{project_id}")
    live.touch(["posts"])
    flash("Your pledge was withdrawn. 👋")
    return projectlink.back_to_post(project_id)


def release(db, milestone, share):
    db.execute("UPDATE project_milestones SET released_at = to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS') WHERE id = ?", (milestone["id"],))
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
