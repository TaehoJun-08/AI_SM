import hashlib
import secrets
from datetime import datetime, timedelta

import live
import re
from flask import (Blueprint, render_template, request,
                   redirect, url_for, session, flash)
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db
import mailer

auth_bp = Blueprint("auth", __name__)

RESET_MINUTES = 30
STAMP = "%Y-%m-%d %H:%M:%S"

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("auth/register.html", form={})

    full_name = request.form.get("full_name", "").strip()
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    db = get_db()

    error = None
    if not full_name or not username or not email or not password:
        error = "All fields are required."
    elif not re.fullmatch(r"[A-Za-z0-9_.]{3,20}", username):
        error = "Usernames are 3-20 characters: letters, numbers, _ and . only."
    elif len(password) < 6:
        error = "Password must be at least 6 characters."
    elif db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        error = f"@{username} is already taken. Try another one."
    elif db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        error = "An account with this email already exists."
    if error:
        flash(error)
        return render_template("auth/register.html", form=request.form)

    db.execute(
        """INSERT INTO users (full_name, username, email, password_hash)
           VALUES (?, ?, ?, ?)""",
        (full_name, username, email, generate_password_hash(password)),
    )
    db.commit()
    live.touch(["users"])
    flash("Account created. Please log in.")
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("auth/login.html")

    email = request.form["email"].strip().lower()
    password = request.form["password"]

    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()

    if user is None or not check_password_hash(user["password_hash"], password):
        flash("Invalid email or password.")
        return render_template("auth/login.html")

    session.clear()
    session["user_id"] = user["id"]
    return redirect(url_for("feed.index"))

#logout

@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


# ---------- forgot password ----------

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


@auth_bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "GET":
        return render_template("auth/forgot.html")

    db = get_db()
    email = request.form.get("email", "").strip().lower()
    user = db.execute("SELECT id, full_name FROM users WHERE email = ? AND is_bot = 0", (email,)).fetchone()
    # Always show the same message whether or not the account exists, so this page can't be
    # used to check which emails are registered.
    generic = "If that email has an account, a reset link is on its way. 📬 Check your inbox (and spam)."
    if user is None:
        flash(generic)
        return redirect(url_for("auth.login"))
    pending = db.execute("SELECT sent_at FROM password_resets WHERE user_id = ?", (user["id"],)).fetchone()
    if pending and datetime.now() - datetime.strptime(pending["sent_at"], STAMP) < timedelta(seconds=60):
        flash(generic)
        return redirect(url_for("auth.login"))
    token = secrets.token_urlsafe(32)
    link = url_for("auth.reset", token=token, _external=True)
    expires = (datetime.now() + timedelta(minutes=RESET_MINUTES)).strftime(STAMP)
    if mailer.send(email, "Reset your Matchmaker password",
                   f"Hi {user['full_name']},\n\nSomeone (hopefully you) asked to reset the password on this "
                   f"account. Open this link within {RESET_MINUTES} minutes to choose a new one:\n{link}\n\n"
                   "If you didn't ask for this, you can ignore this email -- your password stays the same."):
        db.execute("""INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES (?, ?, ?)
                      ON CONFLICT (user_id) DO UPDATE SET token_hash = excluded.token_hash,
                      sent_at = to_char(LOCALTIMESTAMP, 'YYYY-MM-DD HH24:MI:SS'), expires_at = excluded.expires_at""",
                   (user["id"], token_hash(token), expires))
        db.commit()
    flash(generic)
    return redirect(url_for("auth.login"))


@auth_bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    db = get_db()
    pending = db.execute("SELECT * FROM password_resets WHERE token_hash = ?", (token_hash(token),)).fetchone()
    expired = pending is None or datetime.now() > datetime.strptime(pending["expires_at"], STAMP)
    if request.method == "GET":
        return render_template("auth/reset.html", expired=expired)
    if expired:
        flash("That reset link has expired or was already used. Ask for a new one.")
        return redirect(url_for("auth.forgot"))
    password = request.form.get("password", "")
    if len(password) < 6:
        flash("Password must be at least 6 characters.")
        return render_template("auth/reset.html", expired=False)
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
              (generate_password_hash(password), pending["user_id"]))
    db.execute("DELETE FROM password_resets WHERE user_id = ?", (pending["user_id"],))
    db.commit()
    flash("Password changed. Please log in.")
    return redirect(url_for("auth.login"))