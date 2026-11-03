"""The counter-offer coach: a side panel in the deal room where whoever's turn it is can get Nova's
take on a fair counter, grounded in this deal's own history plus how similar deals on the platform
have landed. Adds routes to deals_bp (see deals.py) -- same pattern as servers/extras.py adding
routes to servers_bp."""
import json

from flask import Response, abort, render_template, request, session, stream_with_context

from db import get_db, open_connection
from deals import LABEL, deals_bp, load, role_of, room_context
from helpers import login_required
from ai import gemini


def _history(db, interest_id, user_id):
    return [dict(r) for r in db.execute(
        "SELECT role, body FROM deal_coach_messages WHERE interest_id = ? AND user_id = ? ORDER BY id",
        (interest_id, user_id)).fetchall()]


def _needs_kickoff(db, offer, interest_id, user_id):
    """True the first time this side opens the coach for the CURRENT round -- i.e. they've never
    chatted with it, or their last coach message predates the deal's most recent round."""
    if offer["status"] != "pending":
        return False
    last_round = db.execute("SELECT MAX(created_at) AS t FROM deal_rounds WHERE interest_id = ?",
                            (interest_id,)).fetchone()["t"]
    last_coach = db.execute(
        "SELECT MAX(created_at) AS t FROM deal_coach_messages WHERE interest_id = ? AND user_id = ?",
        (interest_id, user_id)).fetchone()["t"]
    return last_coach is None or (last_round is not None and last_coach < last_round)


@deals_bp.route("/<int:interest_id>/coach")
@login_required
def coach_panel(interest_id):
    db = get_db()
    me = session["user_id"]
    offer = load(db, interest_id)
    if offer is None or role_of(offer, me) is None:
        abort(404)
    ctx = room_context(db, offer, me)
    return render_template("deals/_coach_panel.html", offer=offer, rounds=ctx["rounds"], terms=ctx["terms"],
                           labels=LABEL, chat=_history(db, interest_id, me),
                           kickoff=_needs_kickoff(db, offer, interest_id, me))


@deals_bp.route("/<int:interest_id>/coach/send", methods=["POST"])
@login_required
def coach_send(interest_id):
    db = get_db()
    me = session["user_id"]
    offer = load(db, interest_id)
    if offer is None or role_of(offer, me) is None:
        abort(404)
    kickoff = request.form.get("kickoff") == "1"
    text = (request.form.get("text") or "").strip()[:2000]
    if not kickoff and not text:
        return {"error": "Say something first."}, 400
    history = _history(db, interest_id, me)
    if not kickoff:
        db.execute("INSERT INTO deal_coach_messages (interest_id, user_id, role, body) VALUES (?, ?, 'user', ?)",
                  (interest_id, me, text))
        db.commit()
    user_text = gemini.KICKOFF_PROMPT if kickoff else text

    def generate():
        conn = open_connection()
        try:
            full, completed = "", False
            try:
                for event in gemini.coach_counter_offer(conn, me, interest_id, history, user_text):
                    if event["type"] == "token":
                        full += event["text"]
                    yield json.dumps(event) + "\n"
                completed = True
            finally:
                if full.strip():
                    body = full if completed else full + "\n\n_(stopped)_"
                    conn.execute(
                        "INSERT INTO deal_coach_messages (interest_id, user_id, role, body) VALUES (?, ?, 'model', ?)",
                        (interest_id, me, body))
                    conn.commit()
            if completed:
                yield json.dumps({"type": "done"}) + "\n"
        finally:
            conn.close()

    return Response(stream_with_context(generate()), mimetype="application/x-ndjson")
