from flask import Blueprint, session, abort
from db import get_db
from helpers import login_required, current_user
from projects.routes import STAGES
from reports.pdf import Report, money, send_pdf

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")

STATUS = {"pending": "Pending", "accepted": "Sponsored", "declined": "Declined", "withdrawn": "Withdrawn"}


@reports_bp.route("/project/<int:project_id>.pdf")
@login_required
def project(project_id):
    db = get_db()
    p = db.execute("SELECT * FROM projects WHERE id = ? AND innovator_id = ?",
                   (project_id, session["user_id"])).fetchone()
    if p is None:
        abort(404)
    offers = db.execute(
        """SELECT i.status, i.amount, i.goods, i.pilot, u.full_name, u.username, u.email
           FROM interests i JOIN users u ON u.id = i.sponsor_id
           WHERE i.project_id = ? ORDER BY i.status DESC, i.id""",
        (project_id,),
    ).fetchall()
    likes = db.execute("SELECT COUNT(*) FROM likes WHERE post_id = ?", (project_id,)).fetchone()[0]
    comments = db.execute("SELECT COUNT(*) FROM comments WHERE post_id = ?", (project_id,)).fetchone()[0]
    raised = sum(o["amount"] or 0 for o in offers if o["status"] == "accepted")

    pdf = Report("Project report")
    pdf.title_block(p["title"], f"{p['domain']}  |  {STAGES[p['stage']]}  |  "
                                + (f"{money(p['funding_needed'])} needed" if p["accepts_money"] else "free sponsors welcome"))
    pdf.heading("Pitch")
    pdf.paragraph(p["summary"])
    pdf.heading("Description")
    pdf.paragraph(p["description"])
    pdf.heading("Traction")
    pdf.paragraph(f"{likes} likes  |  {comments} comments  |  " + (f"{money(raised)} raised of {money(p['funding_needed'])}"
                  if p["accepts_money"] else f"{sum(1 for o in offers if o['status'] == 'accepted')} sponsors"))
    pdf.heading("Sponsorship offers")
    pdf.data_table(
        ["Sponsor", "Offer", "Status", "Contact"],
        [[f"{o['full_name']} (@{o['username']})", __import__("deals").describe(o).replace("₹", "Rs.").replace("🌱 ", ""), STATUS[o["status"]],
          o["email"] if o["status"] == "accepted" else "-"] for o in offers],
        (60, 35, 35, 60), "Nobody has offered to sponsor this project yet.")
    pdf.paragraph("Owner: " + current_user()["full_name"])
    return send_pdf(pdf, f"project-{project_id}.pdf")
