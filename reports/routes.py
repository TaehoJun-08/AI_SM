from flask import Blueprint, session, abort
from db import get_db
from helpers import login_required, role_required, current_user
from matching import matches_for_sponsor, matches_for_projects
from projects.routes import STAGES
from reports.pdf import Report, money, send_pdf

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")

STATUS = {"pending": "Pending", "accepted": "Connected", "declined": "Declined"}


@reports_bp.route("/project/<int:project_id>.pdf")
@login_required
@role_required("innovator")
def project(project_id):
    db = get_db()
    p = db.execute("SELECT * FROM projects WHERE id = ? AND innovator_id = ?",
                   (project_id, session["user_id"])).fetchone()
    if p is None:
        abort(404)
    interests = db.execute(
        """SELECT i.status, u.full_name, u.email, s.organisation
           FROM interests i JOIN users u ON u.id = i.sponsor_id
           LEFT JOIN sponsor_profiles s ON s.user_id = i.sponsor_id
           WHERE i.project_id = ? ORDER BY i.status DESC, i.id""",
        (project_id,),
    ).fetchall()
    team = db.execute(
        """SELECT u.full_name FROM project_members m JOIN users u ON u.id = m.user_id
           WHERE m.project_id = ? ORDER BY u.full_name""",
        (project_id,),
    ).fetchall()

    pdf = Report("Project report")
    pdf.title_block(p["title"], f"{p['domain']}  |  {STAGES[p['stage']]}  |  "
                                f"{money(p['funding_needed'])} needed")
    pdf.heading("Pitch")
    pdf.paragraph(p["summary"])
    pdf.heading("Description")
    pdf.paragraph(p["description"])
    pdf.heading("Top sponsor matches")
    pdf.data_table(
        ["Sponsor", "Organisation", "Match", "Why"],
        [[m["sponsor"]["full_name"], m["sponsor"]["organisation"], f"{m['score']}%",
          "; ".join(m["reasons"])] for m in matches_for_projects([p])[p["id"]]],
        (35, 40, 17, 98), "No sponsor matches yet.")
    pdf.heading("Sponsor interest")
    pdf.data_table(
        ["Sponsor", "Status", "Contact"],
        [[i["organisation"] or i["full_name"], STATUS[i["status"]],
          i["email"] if i["status"] == "accepted" else "-"] for i in interests],
        (70, 40, 80), "No sponsor has shown interest yet.")
    pdf.heading("Team")
    names = [current_user()["full_name"] + " (owner)"] + [t["full_name"] for t in team]
    pdf.paragraph(", ".join(names))
    return send_pdf(pdf, f"project-{project_id}.pdf")


@reports_bp.route("/sponsor.pdf")
@login_required
@role_required("sponsor")
def sponsor():
    db = get_db()
    me = current_user()
    profile = db.execute("SELECT * FROM sponsor_profiles WHERE user_id = ?",
                         (me["id"],)).fetchone()
    domains = [r["domain"] for r in db.execute(
        "SELECT domain FROM sponsor_domains WHERE user_id = ? ORDER BY domain", (me["id"],))]
    sent = db.execute(
        """SELECT i.status, p.title, p.funding_needed, u.full_name, u.email
           FROM interests i JOIN projects p ON p.id = i.project_id
           JOIN users u ON u.id = p.innovator_id
           WHERE i.sponsor_id = ? ORDER BY i.status DESC, i.id DESC""",
        (me["id"],),
    ).fetchall()

    pdf = Report("Sponsor portfolio")
    if profile:
        pdf.title_block(profile["organisation"], f"{me['full_name']}  |  Budget "
                        f"{money(profile['budget_min'])} - {money(profile['budget_max'])}")
        pdf.heading("About")
        pdf.paragraph(profile["bio"])
        pdf.heading("Domains")
        pdf.paragraph(", ".join(domains))
    else:
        pdf.title_block(me["full_name"], "No sponsor profile yet")
    pdf.heading("Recommended projects")
    pdf.data_table(
        ["Project", "Domain", "Needs", "Match", "Why"],
        [[m["project"]["title"], m["project"]["domain"], money(m["project"]["funding_needed"]),
          f"{m['score']}%", "; ".join(m["reasons"])]
         for m in matches_for_sponsor(me["id"], limit=10)],
        (38, 26, 28, 16, 82), "No recommendations yet. Complete your profile to get matches.")
    pdf.heading("Your interests")
    pdf.data_table(
        ["Project", "Innovator", "Needs", "Status", "Contact"],
        [[s["title"], s["full_name"], money(s["funding_needed"]), STATUS[s["status"]],
          s["email"] if s["status"] == "accepted" else "-"] for s in sent],
        (40, 32, 28, 26, 64), "You haven't shown interest in any project yet.")
    return send_pdf(pdf, "sponsor-portfolio.pdf")