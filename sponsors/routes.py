from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, session)
from db import get_db
from helpers import login_required, role_required
from projects.routes import DOMAINS, MAX_FUNDING

sponsors_bp = Blueprint("sponsors", __name__, url_prefix="/sponsors")


def validate(form):
    """Check a submitted sponsor profile form. Returns (data, domains, error)."""
    data = {
        "organisation": form.get("organisation", "").strip(),
        "bio": form.get("bio", "").strip(),
        "budget_min": form.get("budget_min", "").strip(),
        "budget_max": form.get("budget_max", "").strip(),
    }
    domains = form.getlist("domains")
    if not all(data.values()):
        return None, None, "Please fill in every field."
    if not domains or not set(domains) <= set(DOMAINS):
        return None, None, "Please choose at least one valid domain."

    budget_min, budget_max = data["budget_min"], data["budget_max"]
    if not (budget_min.isdecimal() and budget_max.isdecimal()):
        return None, None, "Budgets must be whole numbers."
    data["budget_min"], data["budget_max"] = int(budget_min), int(budget_max)
    if not 0 < data["budget_min"] <= data["budget_max"] <= MAX_FUNDING:
        return None, None, "Budget must be between ₹1 and ₹1,000 crore, and min can't be more than max."
    if len(data["organisation"]) > 100:
        return None, None, "Keep the organisation name to 100 characters or less."
    return data, domains, None


def show_form(form=None, chosen=None):
    return render_template("sponsors/profile.html", domains=DOMAINS,
                           form=form or {}, chosen=chosen or [])


@sponsors_bp.route("/profile", methods=["GET", "POST"])
@login_required
@role_required("sponsor")
def profile():
    db = get_db()
    user_id = session["user_id"]
    if request.method == "GET":
        saved = db.execute(
            "SELECT * FROM sponsor_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
        rows = db.execute(
            "SELECT domain FROM sponsor_domains WHERE user_id = ?", (user_id,)
        ).fetchall()
        return show_form(dict(saved) if saved else {},
                         [r["domain"] for r in rows])
    
    data, chosen, error = validate(request.form)
    if error:
        flash(error)
        return show_form(request.form, request.form.getlist("domains"))

    data["user_id"] = user_id
    db.execute(
        """INSERT INTO sponsor_profiles
           (user_id, organisation, bio, budget_min, budget_max)
           VALUES (:user_id, :organisation, :bio, :budget_min, :budget_max)
           ON CONFLICT(user_id) DO UPDATE SET
               organisation = excluded.organisation, bio = excluded.bio,
               budget_min = excluded.budget_min, budget_max = excluded.budget_max,
               updated_at = datetime('now','localtime')""",
        data,
    )
    db.execute("DELETE FROM sponsor_domains WHERE user_id = ?", (user_id,))
    db.executemany(
        "INSERT INTO sponsor_domains (user_id, domain) VALUES (?, ?)",
        [(user_id, d) for d in set(chosen)],
    )
    db.commit()
    flash("Your sponsor profile is saved. ✨")
    return redirect(url_for("dashboard.sponsor"))