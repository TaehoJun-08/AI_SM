import re
from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, session, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db
from helpers import login_required, current_user

profiles_bp = Blueprint("profiles", __name__)

USERNAME_PATTERN = r"[A-Za-z0-9_.]{3,20}"
BIO_LIMIT = 300


@profiles_bp.route("/u/<username>")
@login_required
def public(username):
    person = get_db().execute(
        "SELECT id, full_name, username, role, bio, created_at FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if person is None:
        abort(404)
    return render_template("profiles/public.html", person=person)


@profiles_bp.route("/profile")
@login_required
def edit():
    return render_template("profiles/edit.html")


@profiles_bp.route("/profile/details", methods=["POST"])
@login_required
def save_details():
    me = current_user()
    full_name = request.form.get("full_name", "").strip()
    username = request.form.get("username", "").strip()
    bio = request.form.get("bio", "").strip()
    db = get_db()
    if not full_name or not username:
        flash("Name and username can't be empty.")
    elif not re.fullmatch(USERNAME_PATTERN, username):
        flash("Usernames are 3-20 characters: letters, numbers, _ and . only.")
    elif len(bio) > BIO_LIMIT:
        flash(f"Keep your bio to {BIO_LIMIT} characters or less.")
    elif db.execute("SELECT 1 FROM users WHERE username = ? AND id != ?",
                    (username, me["id"])).fetchone():
        flash(f"@{username} is already taken. Try another one.")
    else:
        db.execute("UPDATE users SET full_name = ?, username = ?, bio = ? WHERE id = ?",
                   (full_name, username, bio, me["id"]))
        db.commit()
        flash("Profile updated. ✨")
    return redirect(url_for("profiles.edit"))


@profiles_bp.route("/profile/password", methods=["POST"])
@login_required
def save_password():
    me = current_user()
    new = request.form.get("new_password", "")
    if not check_password_hash(me["password_hash"], request.form.get("current_password", "")):
        flash("Your current password is wrong, so the password wasn't changed.")
    elif len(new) < 6:
        flash("The new password must be at least 6 characters.")
    elif new != request.form.get("confirm_password", ""):
        flash("The two new passwords don't match.")
    else:
        db = get_db()
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                   (generate_password_hash(new), me["id"]))
        db.commit()
        flash("Password changed. 🔒")
    return redirect(url_for("profiles.edit"))
