"""Trust badges. A person proves they own a college email with a 6-digit code (✓ next to their name), and a
project's owner can ask a faculty mentor to vouch for it: the mentor gets a one-time link and, by confirming,
adds "🎓 Endorsed by ..." to the post. Mentors don't need an account."""
import hashlib
import re
import secrets
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db
from helpers import login_required
import live
import mailer

verify_bp = Blueprint("verify", __name__, url_prefix="/verify")

# Academic domains: anything ending in one of these counts as a college address.
COLLEGE_SUFFIXES = (".edu", ".edu.in", ".ac.in", ".ac.uk", ".edu.au", ".ac.nz", ".edu.sg")
EMAIL_PATTERN = r"[^@\s]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
CODE_MINUTES = 10
MAX_TRIES = 5
MAX_MENTORS = 3
STAMP = "%Y-%m-%d %H:%M:%S"


def college_email(text):
    """The address, lower-cased, if it's a college one; otherwise None."""
    email = (text or "").strip().lower()
    if not re.fullmatch(EMAIL_PATTERN, email) or not email.split("@")[1].endswith(COLLEGE_SUFFIXES):
        return None
    return email


def back():
    return redirect(url_for("settings.index", tab="verify"))


def waiting_code(db, user_id):
    return db.execute("SELECT * FROM email_codes WHERE user_id = ?", (user_id,)).fetchone()


# ---------- the ✓ on your name ----------

@verify_bp.route("/send", methods=["POST"])
@login_required
def send_code():
    db = get_db()
    me = session["user_id"]
    email = college_email(request.form.get("email"))
    pending = waiting_code(db, me)
    if email is None:
        flash("That isn't a college address. Use the one your college gave you (ending in .edu, .ac.in, .edu.in…).")
        return back()
    if pending and datetime.now() - datetime.strptime(pending["sent_at"], STAMP) < timedelta(seconds=60):
        flash("A code was just sent. Wait a minute before asking for another one.")
        return back()
    if db.execute("SELECT 1 FROM users WHERE college_email = ? AND id != ?", (email, me)).fetchone():
        flash("Another account already verified that address.")
        return back()
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = (datetime.now() + timedelta(minutes=CODE_MINUTES)).strftime(STAMP)
    if not mailer.send(email, f"{code} is your Matchmaker verification code",
                       f"Your code is {code}\n\nType it on the Verification page within {CODE_MINUTES} minutes. "
                       "If you didn't ask for this, you can ignore this email."):
        flash("The email couldn't be sent. Email isn't set up on this server yet (see README).")
        return back()
    db.execute("""INSERT INTO email_codes (user_id, email, code_hash, expires_at) VALUES (?, ?, ?, ?)
                  ON CONFLICT (user_id) DO UPDATE SET email = excluded.email, code_hash = excluded.code_hash, tries = 0,
                  sent_at = to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'), expires_at = excluded.expires_at""",
               (me, email, generate_password_hash(code), expires))
    db.commit()
    flash(f"Code sent to {email}. 📬 Check your inbox (and spam).")
    return back()


@verify_bp.route("/check", methods=["POST"])
@login_required
def check_code():
    db = get_db()
    me = session["user_id"]
    pending = waiting_code(db, me)
    code = request.form.get("code", "").strip()
    if pending is None or datetime.now() > datetime.strptime(pending["expires_at"], STAMP):
        flash("That code has expired. Ask for a new one.")
    elif pending["tries"] >= MAX_TRIES:
        flash("Too many wrong tries. Ask for a new code.")
    elif not check_password_hash(pending["code_hash"], code):
        db.execute("UPDATE email_codes SET tries = tries + 1 WHERE user_id = ?", (me,))
        db.commit()
        flash("That code isn't right. Check the email and try again.")
    else:
        db.execute("UPDATE users SET college_email = ?, verified_at = to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS') WHERE id = ?",
                   (pending["email"], me))
        db.execute("DELETE FROM email_codes WHERE user_id = ?", (me,))
        db.commit()
        live.user_changed(me)
        live.touch(["posts", "users"])
        flash("Verified! ✓ Your badge now shows next to your name.")
    return back()


@verify_bp.route("/remove", methods=["POST"])
@login_required
def remove():
    db = get_db()
    db.execute("UPDATE users SET college_email = NULL, verified_at = NULL WHERE id = ?", (session["user_id"],))
    db.execute("DELETE FROM email_codes WHERE user_id = ?", (session["user_id"],))
    db.commit()
    live.touch(["posts", "users"])
    flash("Your college email was removed, and so was the badge.")
    return back()


# ---------- a faculty mentor vouches for a project ----------

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


@verify_bp.route("/mentor/<int:project_id>", methods=["POST"])
@login_required
def ask_mentor(project_id):
    db = get_db()
    project = db.execute("SELECT p.*, u.full_name FROM projects p JOIN users u ON u.id = p.innovator_id "
                         "WHERE p.id = ? AND p.innovator_id = ?", (project_id, session["user_id"])).fetchone()
    if project is None:
        abort(404)
    name = " ".join(request.form.get("name", "").split())[:60]
    email = college_email(request.form.get("email"))
    back_to_post = redirect(url_for("feed.post", post_id=project_id))
    if not name or email is None:
        flash("Give your mentor's name and their college email (ending in .edu, .ac.in, .edu.in…).")
        return back_to_post
    if db.execute("SELECT COUNT(*) FROM project_endorsements WHERE project_id = ?", (project_id,)).fetchone()[0] >= MAX_MENTORS:
        flash(f"A project can ask up to {MAX_MENTORS} mentors.")
        return back_to_post
    if db.execute("SELECT 1 FROM project_endorsements WHERE project_id = ? AND mentor_email = ?", (project_id, email)).fetchone():
        flash("You've already asked that mentor.")
        return back_to_post
    token = secrets.token_urlsafe(32)
    link = url_for("verify.endorse", token=token, _external=True)
    if not mailer.send(email, f"{project['full_name']} asks you to endorse “{project['title']}”",
                       f"Dear {name},\n\n{project['full_name']} listed you as a faculty mentor for their project "
                       f"“{project['title']}” on Matchmaker, a platform where students find sponsors.\n\n"
                       f"If you know this project and are happy to vouch for it, open this link and confirm:\n{link}\n\n"
                       "Your name and college domain will show on the project. If you don't know it, just ignore this email."):
        flash("The email couldn't be sent. Email isn't set up on this server yet (see README).")
        return back_to_post
    db.execute("INSERT INTO project_endorsements (project_id, mentor_name, mentor_email, token_hash) VALUES (?, ?, ?, ?)",
               (project_id, name, email, token_hash(token)))
    db.commit()
    live.touch(["posts"])
    flash(f"Request sent to {email}. 🎓 The badge appears once they confirm.")
    return back_to_post


def endorsement_for(token):
    row = get_db().execute("""SELECT e.*, p.title, p.summary, u.full_name FROM project_endorsements e
                              JOIN projects p ON p.id = e.project_id JOIN users u ON u.id = p.innovator_id
                              WHERE e.token_hash = ?""", (token_hash(token),)).fetchone()
    if row is None:
        abort(404)
    return row


@verify_bp.route("/endorse/<token>")
def endorse(token):
    """The mentor's link. Opening it only shows the project; confirming is a button press, so email scanners
    that open links on their own can't endorse anything."""
    return render_template("verify/endorse.html", e=endorsement_for(token), token=token)


@verify_bp.route("/endorse/<token>", methods=["POST"])
def confirm_endorsement(token):
    e = endorsement_for(token)
    db = get_db()
    if e["status"] == "pending":
        db.execute("UPDATE project_endorsements SET status = 'endorsed', endorsed_at = to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS') WHERE id = ?",
                   (e["id"],))
        db.commit()
        owner = db.execute("SELECT innovator_id FROM projects WHERE id = ?", (e["project_id"],)).fetchone()["innovator_id"]
        live.notify([owner], "activity", "🎓 Endorsed", f"{e['mentor_name']} endorsed {e['title']}", f"/p/{e['project_id']}")
        live.touch(["posts"])
    return render_template("verify/endorse.html", e=endorsement_for(token), token=token, done=True)
